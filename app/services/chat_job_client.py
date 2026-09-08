from __future__ import annotations

import json
from uuid import UUID

import httpx


class ChatJobClient:
    def __init__(self, base_url: str, jobs_path: str, timeout_sec: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.jobs_path = jobs_path
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_job(self, job_id: UUID) -> dict:
        response = await self._client.get(
            f"{self.base_url}{self.jobs_path}/{job_id}",
        )
        response.raise_for_status()
        return response.json()

    async def save_prepared(
        self,
        job_id: UUID,
        context: str,
        sources: list[dict],
    ) -> None:
        response = await self._client.put(
            f"{self.base_url}{self.jobs_path}/{job_id}/prepared",
            json={
                "context": context,
                "sources": json.dumps(sources, ensure_ascii=False),
            },
        )
        response.raise_for_status()

    async def mark_waiting(self, job_id: UUID) -> None:
        response = await self._client.put(
            f"{self.base_url}{self.jobs_path}/{job_id}/waiting",
        )
        response.raise_for_status()

    async def mark_processing(self, job_id: UUID) -> None:
        response = await self._client.put(
            f"{self.base_url}{self.jobs_path}/{job_id}/processing",
        )
        response.raise_for_status()

    async def mark_completed(self, job_id: UUID, answer: str) -> None:
        response = await self._client.put(
            f"{self.base_url}{self.jobs_path}/{job_id}/completed",
            json={"answer": answer},
        )
        response.raise_for_status()

    async def mark_failed(self, job_id: UUID, error_message: str) -> None:
        response = await self._client.put(
            f"{self.base_url}{self.jobs_path}/{job_id}/failed",
            json={"errorMessage": error_message},
        )
        response.raise_for_status()
