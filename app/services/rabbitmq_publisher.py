from __future__ import annotations

import json

import aio_pika
from aio_pika.abc import AbstractRobustChannel, AbstractRobustConnection


class RabbitMQPublisher:
    def __init__(self, url: str, queue_name: str) -> None:
        self.url = url
        self.queue_name = queue_name
        self.connection: AbstractRobustConnection | None = None
        self.channel: AbstractRobustChannel | None = None

    async def connect(self) -> None:
        connection = await aio_pika.connect_robust(self.url, timeout=5)
        try:
            channel = await connection.channel(
                publisher_confirms=True,
                on_return_raises=True,
            )
            await channel.declare_queue(self.queue_name, durable=True)
        except BaseException:
            await connection.close()
            raise

        self.connection = connection
        self.channel = channel
        print(
            f"[RABBITMQ] status=connected queue={self.queue_name}",
            flush=True,
        )

    async def close(self) -> None:
        if self.connection is not None and not self.connection.is_closed:
            await self.connection.close()
        self.connection = None
        self.channel = None

    async def publish(self, payload: dict[str, str]) -> None:
        if self.channel is None or self.channel.is_closed:
            raise RuntimeError("RabbitMQ publisher is not connected")

        await self.channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=self.queue_name,
            mandatory=True,
        )
        print(
            f"[RABBITMQ] status=published job_id={payload['job_id']}",
            flush=True,
        )
