from __future__ import annotations

import asyncio
import json
import base64
from time import perf_counter
from uuid import UUID, uuid4

import aio_pika
import httpx

from app.core.config import get_settings
from app.services.chat_job_client import ChatJobClient
from app.services.llm_service import LlmService
from app.services.rabbitmq_publisher import RabbitMQPublisher


LLM_FAILURE_ANSWERS = (
    "LLM 응답 시간이 초과되었습니다.",
    "LLM 서버에서 오류 응답을 반환했습니다.",
    "LLM 서버에 연결할 수 없습니다.",
    "LLM이 빈 응답을 반환했습니다.",
)
TERMINAL = {"COMPLETED", "FAILED"}


async def retry_api(operation, settings):
    """Bound each HTTP operation; never retry a permanent error or claim conflict."""
    for attempt in range(settings.job_api_attempts):
        try:
            return await operation()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            retryable = (not isinstance(exc, httpx.HTTPStatusError)
                         or exc.response.status_code in {408, 429}
                         or exc.response.status_code >= 500)
            if not retryable or attempt + 1 == settings.job_api_attempts:
                raise
            await asyncio.sleep(settings.job_retry_delay_sec * (2 ** attempt))


async def heartbeat(client, job_id, attempt_id, settings):
    while True:
        await asyncio.sleep(settings.job_heartbeat_sec)
        # Any exhausted heartbeat error stops local work conservatively. The DB
        # lease, not a local wall clock, is authoritative for subsequent recovery.
        await retry_api(lambda: client.heartbeat(job_id, attempt_id), settings)
        print(f"[CHAT_JOB] job_id={job_id} phase=heartbeat attempt_id={attempt_id}", flush=True)


async def finish_claimed(client, llm, job_id, attempt_id, job, settings):
    """Generation failure and completion transport failure have different outcomes."""
    try:
        answer = await asyncio.wait_for(llm.generate_answer(
            question=job["question"], context=job["context"]), settings.job_max_runtime_sec)
        if not answer.strip() or answer.startswith(LLM_FAILURE_ANSWERS):
            raise RuntimeError("LlmService returned a failure response")
    except Exception as exc:
        print(f"[CHAT_JOB] job_id={job_id} phase=generation_failed error={type(exc).__name__}", flush=True)
        operation = lambda: client.mark_failed(job_id, "LLM 작업 처리에 실패했습니다.", attempt_id)
    else:
        # Keep the generated answer during retries; do not regenerate or notify
        # FAILED just because the completion response was lost.
        operation = lambda: client.mark_completed(job_id, answer, attempt_id)
    try:
        await retry_api(operation, settings)
    except (httpx.RequestError, httpx.HTTPStatusError):
        current = await retry_api(lambda: client.get_job(job_id), settings)
        if current.get("status") in TERMINAL:
            return
        if current.get("status") != "PROCESSING" or current.get("attemptId") != str(attempt_id):
            # Recovery has revoked this attempt. Never update the new owner's job.
            return
        raise


async def park_message(message, publisher, job_id, attempt_id, reason):
    # Confirmation must precede ACK. A failure propagates, closes the consumer
    # connection and leaves the original delivery unacked for broker redelivery.
    await publisher.publish({
        "job_id": str(job_id) if job_id else "",
        "attempt_id": str(attempt_id) if attempt_id else "",
        "reason": reason,
        "body_base64": base64.b64encode(message.body).decode("ascii"),
    }, dead_letter=True)
    await message.ack()
    print(f"[RABBITMQ_WORKER] status=parked job_id={job_id} reason={reason}", flush=True)


async def recover_jobs(client, publisher, settings):
    cursor = 0
    failures = 0
    while True:
        try:
            page = await retry_api(lambda: client.recover(cursor, settings.job_recovery_batch_size), settings)
            for job_id in page["jobIds"]:
                await publisher.publish({"job_id": job_id})
            cursor = page["nextCursor"]
            failures = 0
        except Exception as exc:
            # Persisted WAITING throttle ensures a failed publish is retried by
            # a later sweep, including after this worker is killed.
            cursor = 0
            failures = min(failures + 1, 5)
            print(f"[CHAT_JOB] phase=recovery_error error={type(exc).__name__}", flush=True)
        await asyncio.sleep(min(60, settings.job_recovery_interval_sec * (2 ** failures)))


async def process_message(message, client, llm, settings, publisher):
    started = perf_counter()
    try:
        payload = json.loads(message.body.decode("utf-8"))
        if not isinstance(payload, dict) or not payload.get("job_id"):
            raise ValueError("message must contain job_id")
        job_id = UUID(str(payload["job_id"]))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        await park_message(message, publisher, None, None, "invalid_message")
        return
    print(f"[RABBITMQ_WORKER] status=received job_id={job_id}", flush=True)
    attempt_id = uuid4()
    try:
        job = await retry_api(lambda: client.get_job(job_id), settings)
        for _ in range(20):
            if job.get("status") != "PREPARING":
                break
            await asyncio.sleep(0.1)
            job = await retry_api(lambda: client.get_job(job_id), settings)
        if job.get("status") in TERMINAL | {"PROCESSING"}:
            print(f"[RABBITMQ_WORKER] status=skipped job_id={job_id} job_status={job.get('status')}", flush=True)
        elif job.get("status") != "WAITING":
            raise ValueError("Job is not ready")
        else:
            try:
                # The UUID is stable across HTTP retries, but unique per delivery.
                job = await retry_api(lambda: client.mark_processing(job_id, attempt_id), settings)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:
                    raise
                print(f"[CHAT_JOB] job_id={job_id} phase=claim_failed status_code=409", flush=True)
            else:
                print(f"[CHAT_JOB] job_id={job_id} status=PROCESSING attempt_id={attempt_id}", flush=True)
                work = asyncio.create_task(finish_claimed(client, llm, job_id, attempt_id, job, settings))
                pulse = asyncio.create_task(heartbeat(client, job_id, attempt_id, settings))
                try:
                    done, _ = await asyncio.wait({work, pulse}, return_when=asyncio.FIRST_COMPLETED)
                    if work in done:
                        await work
                    else:
                        await pulse  # Ownership/API failure cancels the local generation.
                finally:
                    work.cancel()
                    pulse.cancel()
                    await asyncio.gather(work, pulse, return_exceptions=True)
    except Exception as exc:
        print(f"[CHAT_JOB] job_id={job_id} phase=delivery_failed error={type(exc).__name__}", flush=True)
        await park_message(message, publisher, job_id, attempt_id, type(exc).__name__)
        return
    await message.ack()
    print(f"[RABBITMQ_WORKER] status=acked job_id={job_id} elapsed_sec={perf_counter() - started:.3f}", flush=True)


async def main() -> None:
    settings = get_settings()
    client = ChatJobClient(base_url=settings.spring_base_url,
                           jobs_path=settings.spring_chat_jobs_path,
                           timeout_sec=settings.job_api_timeout_sec)
    llm = LlmService()
    publisher = RabbitMQPublisher(settings.rabbitmq_url, settings.rabbitmq_queue, settings.rabbitmq_dlq)
    recovery = None
    try:
        await publisher.connect()
        connection = await aio_pika.connect_robust(settings.rabbitmq_url, timeout=5)
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=1)
            queue = await channel.declare_queue(settings.rabbitmq_queue, durable=True)
            recovery = asyncio.create_task(recover_jobs(client, publisher, settings))
            print(f"[RABBITMQ_WORKER] status=started queue={settings.rabbitmq_queue} prefetch_count=1", flush=True)
            try:
                async with queue.iterator() as messages:
                    async for message in messages:
                        await process_message(message, client, llm, settings, publisher)
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
    finally:
        await publisher.close()
        await llm.close()
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
