"""Actual worker source with isolated HTTP, LLM and broker dependencies (no DB)."""
import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import UUID

import httpx


class RabbitMqWorkerCompletionResponseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.job_id = UUID('11111111-1111-4111-8111-111111111111')
        self.answer = 'A valid generated answer.'
        self.job = {'status': 'WAITING', 'question': 'question', 'context': 'context'}
        self.settings = SimpleNamespace(job_api_attempts=3, job_retry_delay_sec=0,
            job_heartbeat_sec=10, job_max_runtime_sec=2, job_api_timeout_sec=1,
            spring_base_url='http://unused.test', spring_chat_jobs_path='/api/internal/chat/jobs',
            rabbitmq_url='amqp://unused.test/', rabbitmq_queue='test.jobs', rabbitmq_dlq='test.jobs.dlq',
            job_recovery_interval_sec=30, job_recovery_batch_size=100)
        def claim(job_id, attempt_id):
            self.job = {**self.job, 'status': 'PROCESSING', 'attemptId': str(attempt_id)}
            return self.job
        self.client = SimpleNamespace(get_job=AsyncMock(side_effect=lambda _: self.job),
            mark_processing=AsyncMock(side_effect=claim), mark_completed=AsyncMock(),
            mark_failed=AsyncMock(), heartbeat=AsyncMock(), close=AsyncMock())
        self.client.recover = AsyncMock(return_value={'jobIds': [], 'nextCursor': 0})
        self.publisher = SimpleNamespace(publish=AsyncMock(), connect=AsyncMock(), close=AsyncMock())
        self.llm = SimpleNamespace(generate_answer=AsyncMock(return_value=self.answer), close=AsyncMock())
        self.message = SimpleNamespace(body=json.dumps({'job_id': str(self.job_id)}).encode(),
                                       ack=AsyncMock(), reject=AsyncMock())
        messages = MagicMock()
        messages.__aiter__.return_value = [self.message]
        iterator = MagicMock()
        iterator.__aenter__.return_value = messages
        self.queue = SimpleNamespace(iterator=Mock(return_value=iterator))
        self.channel = SimpleNamespace(set_qos=AsyncMock(), declare_queue=AsyncMock(return_value=self.queue))
        self.connection = MagicMock()
        self.connection.__aenter__.return_value = self.connection
        self.connection.channel = AsyncMock(return_value=self.channel)
        modules = {}
        for name, attr, value in [
            ('app.core.config', 'get_settings', Mock(return_value=self.settings)),
            ('app.services.chat_job_client', 'ChatJobClient', Mock(return_value=self.client)),
            ('app.services.llm_service', 'LlmService', Mock(return_value=self.llm)),
            ('app.services.rabbitmq_publisher', 'RabbitMQPublisher', Mock(return_value=self.publisher)),
            ('aio_pika', 'connect_robust', AsyncMock(return_value=self.connection)),
        ]:
            module = ModuleType(name)
            setattr(module, attr, value)
            modules[name] = module
        source = Path(__file__).resolve().parents[1] / 'app/workers/rabbitmq_worker.py'
        spec = importlib.util.spec_from_file_location('_worker_under_test', source)
        self.worker = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.worker)

    def error(self, status=None):
        request = httpx.Request('PUT', 'http://unused.test/job')
        return (httpx.ReadTimeout('injected', request=request) if status is None else
                httpx.HTTPStatusError('injected', request=request, response=httpx.Response(status, request=request)))

    async def run_message(self):
        with redirect_stdout(io.StringIO()):
            await asyncio.wait_for(self.worker.process_message(self.message, self.client, self.llm, self.settings, self.publisher), 3)

    async def test_normal_completion_acknowledges_without_failure_notification(self):
        await self.run_message()
        attempt = self.client.mark_processing.await_args.args[1]
        self.client.mark_completed.assert_awaited_once_with(self.job_id, self.answer, attempt)
        self.client.mark_failed.assert_not_awaited()
        self.message.ack.assert_awaited_once()
        self.message.reject.assert_not_awaited()

    async def test_modeled_completion_then_read_timeout_reconciles_without_failure(self):
        def lost(*args):
            self.job['status'] = 'COMPLETED'
            raise self.error()
        self.client.mark_completed.side_effect = lost
        await self.run_message()
        self.assertEqual(3, self.client.mark_completed.await_count)
        self.llm.generate_answer.assert_awaited_once()
        self.client.mark_failed.assert_not_awaited()
        self.message.ack.assert_awaited_once()

    async def test_failure_notification_exception_preserves_delivery(self):
        self.llm.generate_answer.side_effect = RuntimeError('generation failed')
        self.client.mark_failed.side_effect = self.error(503)
        await self.run_message()
        self.assertEqual(3, self.client.mark_failed.await_count)
        self.client.mark_completed.assert_not_awaited()
        self.publisher.publish.assert_awaited_once()
        self.message.reject.assert_not_awaited()
        self.message.ack.assert_awaited_once()

    async def test_claim_conflict_acks_without_generation_or_failure_notification(self):
        self.client.mark_processing.side_effect = self.error(409)
        await self.run_message()
        self.client.mark_processing.assert_awaited_once()
        self.llm.generate_answer.assert_not_awaited()
        self.client.mark_failed.assert_not_awaited()
        self.message.ack.assert_awaited_once()

    async def test_transient_claim_errors_retry_same_attempt_without_failed(self):
        for status in (None, 408, 429, 503):
            with self.subTest(status=status):
                await self.asyncSetUp()
                self.client.mark_processing.side_effect = self.error(status)
                await self.run_message()
                self.assertEqual(3, self.client.mark_processing.await_count)
                self.assertEqual(1, len({str(c.args[1]) for c in self.client.mark_processing.await_args_list}))
                self.llm.generate_answer.assert_not_awaited()
                self.client.mark_failed.assert_not_awaited()
                self.publisher.publish.assert_awaited_once()
                self.message.reject.assert_not_awaited()

    async def test_nonretryable_claim_error_is_not_retried_or_reported_failed(self):
        self.client.mark_processing.side_effect = self.error(400)
        await self.run_message()
        self.client.mark_processing.assert_awaited_once()
        self.llm.generate_answer.assert_not_awaited()
        self.client.mark_failed.assert_not_awaited()

    async def test_transient_lookup_failure_has_bounded_retries(self):
        self.client.get_job.side_effect = self.error(503)
        await self.run_message()
        self.assertEqual(3, self.client.get_job.await_count)
        self.client.mark_processing.assert_not_awaited()
        self.llm.generate_answer.assert_not_awaited()
        self.message.ack.assert_awaited_once()

    async def test_real_generation_failure_still_notifies_failed_and_acks(self):
        self.llm.generate_answer.side_effect = RuntimeError('generation failed')
        await self.run_message()
        self.client.mark_failed.assert_awaited_once_with(self.job_id, 'LLM 작업 처리에 실패했습니다.',
                                                       self.client.mark_processing.await_args.args[1])
        self.message.ack.assert_awaited_once()

    async def test_completion_transport_failure_never_becomes_generation_failure(self):
        self.client.mark_completed.side_effect = self.error(503)
        await self.run_message()
        self.assertEqual(3, self.client.mark_completed.await_count)
        self.client.mark_failed.assert_not_awaited()
        self.llm.generate_answer.assert_awaited_once()
        self.message.ack.assert_awaited_once()

    async def test_terminal_and_processing_deliveries_do_not_generate(self):
        for status in ('COMPLETED', 'FAILED', 'PROCESSING'):
            with self.subTest(status=status):
                await self.asyncSetUp()
                self.job['status'] = status
                await self.run_message()
                self.client.mark_processing.assert_not_awaited()
                self.llm.generate_answer.assert_not_awaited()
                self.message.ack.assert_awaited_once()

    async def test_long_generation_keeps_heartbeating(self):
        self.settings.job_heartbeat_sec = .005
        async def slow(**kwargs):
            await asyncio.sleep(.04)
            return self.answer
        self.llm.generate_answer.side_effect = slow
        await self.run_message()
        self.assertGreaterEqual(self.client.heartbeat.await_count, 2)
        self.client.mark_completed.assert_awaited_once()

    async def test_lost_ownership_cancels_generation_without_failure_callback(self):
        self.settings.job_heartbeat_sec = .005
        cancelled = asyncio.Event()
        async def hang(**kwargs):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        self.llm.generate_answer.side_effect = hang
        self.client.heartbeat.side_effect = self.error(409)
        await self.run_message()
        self.assertTrue(cancelled.is_set())
        self.client.mark_completed.assert_not_awaited()
        self.client.mark_failed.assert_not_awaited()

    async def test_hard_runtime_limit_notifies_failed(self):
        self.settings.job_max_runtime_sec = .01
        self.llm.generate_answer.side_effect = lambda **kwargs: None
        async def hang(**kwargs):
            await asyncio.sleep(10)
        self.llm.generate_answer.side_effect = hang
        await self.run_message()
        self.client.mark_failed.assert_awaited_once()
        self.message.ack.assert_awaited_once()

    async def test_main_preserves_prefetch_and_closes_dependencies(self):
        with redirect_stdout(io.StringIO()):
            await self.worker.main()
        self.channel.set_qos.assert_awaited_once_with(prefetch_count=1)
        self.channel.declare_queue.assert_awaited_once_with('test.jobs', durable=True)
        self.client.close.assert_awaited_once()
        self.llm.close.assert_awaited_once()

    async def test_dlq_publish_failure_does_not_ack_or_discard(self):
        self.client.get_job.side_effect = self.error(503)
        self.publisher.publish.side_effect = RuntimeError('broker unavailable')
        with self.assertRaises(RuntimeError):
            await self.run_message()
        self.message.ack.assert_not_awaited()
        self.message.reject.assert_not_awaited()

    async def test_invalid_message_is_parked_with_original_body(self):
        self.message.body = b'not json'
        await self.run_message()
        self.assertTrue(self.publisher.publish.await_args.kwargs['dead_letter'])
        self.assertEqual('bm90IGpzb24=', self.publisher.publish.await_args.args[0]['body_base64'])
        self.message.ack.assert_awaited_once()
        self.llm.generate_answer.assert_not_awaited()

    async def test_recovery_publishes_database_candidates(self):
        self.client.recover.return_value = {'jobIds': [str(self.job_id)], 'nextCursor': 0}
        published = asyncio.Event()
        self.publisher.publish.side_effect = lambda *a, **k: published.set()
        task = asyncio.create_task(self.worker.recover_jobs(self.client, self.publisher, self.settings))
        try:
            await asyncio.wait_for(published.wait(), 1)
            self.publisher.publish.assert_awaited_once_with({'job_id': str(self.job_id)})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_dlq_confirmation_precedes_ack(self):
        events = []
        self.client.get_job.side_effect = self.error(503)
        self.publisher.publish.side_effect = lambda *a, **k: events.append('confirmed')
        self.message.ack.side_effect = lambda: events.append('acked')
        await self.run_message()
        self.assertEqual(['confirmed', 'acked'], events)
