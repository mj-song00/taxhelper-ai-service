from __future__ import annotations

import asyncio
from collections import OrderedDict
from time import monotonic

import httpx

from app.core.config import get_settings


class EmbeddingService:
    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.embedding_enabled
        self.api_key = settings.openai_api_key
        self.base_url = settings.openai_base_url.rstrip("/")
        self.model = settings.openai_embedding_model
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.openai_embedding_timeout_sec)
        )
        self._cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._cache_ttl_sec = settings.search_cache_ttl_sec
        self._cache_max_entries = settings.search_cache_max_entries

    async def close(self) -> None:
        await self._client.aclose()

    async def embed_query(self, text: str) -> list[float] | None:
        text = " ".join(text.split())
        if not self.enabled or not self.api_key or not text:
            return None

        cached = await self._get_cached(text)
        if cached is not None:
            return cached

        try:
            response = await self._client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": text},
            )
            response.raise_for_status()
            data = response.json()
            embedding = data["data"][0]["embedding"]
            if not isinstance(embedding, list):
                return None
            vector = [float(value) for value in embedding]
            await self._set_cached(text, vector)
            return vector
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            print(
                f"[EMBEDDING] status=failed model={self.model} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
            return None

    async def _get_cached(self, text: str) -> list[float] | None:
        if self._cache_ttl_sec <= 0 or self._cache_max_entries <= 0:
            return None

        async with self._cache_lock:
            cached = self._cache.get(text)
            if cached is None:
                return None
            cached_at, vector = cached
            if monotonic() - cached_at > self._cache_ttl_sec:
                self._cache.pop(text, None)
                return None
            self._cache.move_to_end(text)
            return list(vector)

    async def _set_cached(self, text: str, vector: list[float]) -> None:
        if self._cache_ttl_sec <= 0 or self._cache_max_entries <= 0:
            return

        async with self._cache_lock:
            self._cache[text] = (monotonic(), list(vector))
            self._cache.move_to_end(text)
            while len(self._cache) > self._cache_max_entries:
                self._cache.popitem(last=False)
