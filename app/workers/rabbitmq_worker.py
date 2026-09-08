from __future__ import annotations

import asyncio
import json
from time import perf_counter
from uuid import UUID

import aio_pika
import httpx

from app.core.config import get_settings
from app.services.chat_job_client import ChatJobClient
from app.services.llm_service import LlmService


LLM_FAILURE_ANSWERS = (
    "LLM 응답 시간이 초과되었습니다.",
    "LLM 서버에서 오류 응답을 반환했습니다.",
    "LLM 서버에 연결할 수 없습니다.",
    "LLM이 빈 응답을 반환했습니다.",
)


async def main() -> None:
    settings = get_settings()
    chat_job_client = ChatJobClient(
        base_url=settings.spring_base_url,
        jobs_path=settings.spring_chat_jobs_path,
        timeout_sec=settings.request_timeout_sec,
    )
    llm_service = LlmService()
    connection = await aio_pika.connect_robust(settings.rabbitmq_url, timeout=5)

    try:
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=1)
            queue = await channel.declare_queue(
                settings.rabbitmq_queue,
                durable=True,
            )
            print(
                f"[RABBITMQ_WORKER] status=started queue={settings.rabbitmq_queue} "
                "prefetch_count=1",
                flush=True,
            )

            async with queue.iterator() as messages:
                async for message in messages:
                    job_started_at = perf_counter()
                    try:
                        payload = json.loads(message.body.decode("utf-8"))
                        if not isinstance(payload, dict) or not payload.get("job_id"):
                            raise ValueError("message must contain job_id")
                        job_id = UUID(str(payload["job_id"]))
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                        print(
                            f"[RABBITMQ_WORKER] status=rejected "
                            f"error={type(exc).__name__}: {exc}",
                            flush=True,
                        )
                        await message.reject(requeue=False)
                        continue

                    print(
                        f"[RABBITMQ_WORKER] status=received job_id={job_id}",
                        flush=True,
                    )

                    try:
                        job = await chat_job_client.get_job(job_id)
                        # Publish confirm precedes the WAITING update, so a fast
                        # consumer can briefly observe PREPARING for the same job.
                        for _ in range(20):
                            if job.get("status") != "PREPARING":
                                break
                            await asyncio.sleep(0.1)
                            job = await chat_job_client.get_job(job_id)
                    except httpx.HTTPStatusError as exc:
                        status_code = exc.response.status_code
                        print(
                            f"[RABBITMQ_WORKER] status=rejected job_id={job_id} "
                            f"reason={'job_not_found' if status_code == 404 else 'job_lookup_failed'} "
                            f"status_code={status_code}",
                            flush=True,
                        )
                        await message.reject(requeue=False)
                        continue
                    except httpx.RequestError as exc:
                        print(
                            f"[RABBITMQ_WORKER] status=rejected job_id={job_id} "
                            f"reason=job_lookup_failed error={type(exc).__name__}",
                            flush=True,
                        )
                        await message.reject(requeue=False)
                        continue

                    job_status = job.get("status")
                    if job_status in {"COMPLETED", "FAILED", "PROCESSING"}:
                        print(
                            f"[RABBITMQ_WORKER] status=skipped job_id={job_id} "
                            f"job_status={job_status}",
                            flush=True,
                        )
                        await message.ack()
                        print(
                            f"[RABBITMQ_WORKER] status=acked job_id={job_id}",
                            flush=True,
                        )
                        continue

                    if job_status != "WAITING":
                        print(
                            f"[RABBITMQ_WORKER] status=rejected job_id={job_id} "
                            f"reason=invalid_job_status job_status={job_status}",
                            flush=True,
                        )
                        await message.reject(requeue=False)
                        continue

                    try:
                        await chat_job_client.mark_processing(job_id)
                        print(
                            f"[CHAT_JOB] job_id={job_id} status=PROCESSING",
                            flush=True,
                        )
                        llm_started_at = perf_counter()
                        answer = await llm_service.generate_answer(
                            question=job["question"],
                            context=job["context"],
                        )
                        llm_elapsed_sec = perf_counter() - llm_started_at
                        if not answer.strip() or answer.startswith(LLM_FAILURE_ANSWERS):
                            raise RuntimeError("LlmService returned a failure response")

                        await chat_job_client.mark_completed(job_id, answer)
                        print(
                            f"[CHAT_JOB] job_id={job_id} status=COMPLETED "
                            f"llm_elapsed_sec={llm_elapsed_sec:.3f} "
                            f"job_elapsed_sec={perf_counter() - job_started_at:.3f}",
                            flush=True,
                        )
                    except Exception as exc:
                        failure_message = "LLM 작업 처리에 실패했습니다."
                        try:
                            await chat_job_client.mark_failed(job_id, failure_message)
                        except Exception as status_exc:
                            print(
                                f"[CHAT_JOB] job_id={job_id} status=failed_update_error "
                                f"error={type(status_exc).__name__}",
                                flush=True,
                            )
                            await message.reject(requeue=False)
                            continue
                        print(
                            f"[CHAT_JOB] job_id={job_id} status=FAILED "
                            f"error={type(exc).__name__}",
                            flush=True,
                        )

                    await message.ack()
                    print(
                        f"[RABBITMQ_WORKER] status=acked job_id={job_id}",
                        flush=True,
                    )
    finally:
        await llm_service.close()
        await chat_job_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
