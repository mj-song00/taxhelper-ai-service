from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from time import monotonic
from typing import Any

import httpx


class ChunkSearchClient:
    def __init__(
        self,
        base_url: str,
        chunks_path: str,
        timeout_sec: float,
        precedent_chunks_path: str | None = None,
        cache_ttl_sec: float = 300.0,
        cache_max_entries: int = 256,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chunks_path = chunks_path
        self.precedent_chunks_path = precedent_chunks_path or chunks_path
        self.timeout_sec = timeout_sec
        self.cache_ttl_sec = cache_ttl_sec
        self.cache_max_entries = cache_max_entries
        self._client = httpx.AsyncClient(timeout=timeout_sec)
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, tuple[list[dict[str, Any]], dict[str, Any]]]] = OrderedDict()
        self._cache_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_cached(
        self,
        key: tuple[Any, ...],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        if self.cache_ttl_sec <= 0 or self.cache_max_entries <= 0:
            return None

        async with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None

            cached_at, value = cached
            if monotonic() - cached_at > self.cache_ttl_sec:
                self._cache.pop(key, None)
                return None

            self._cache.move_to_end(key)
            return deepcopy(value)

    async def _set_cached(
        self,
        key: tuple[Any, ...],
        value: tuple[list[dict[str, Any]], dict[str, Any]],
    ) -> None:
        if self.cache_ttl_sec <= 0 or self.cache_max_entries <= 0:
            return

        async with self._cache_lock:
            self._cache[key] = (monotonic(), deepcopy(value))
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max_entries:
                self._cache.popitem(last=False)

    async def fetch_chunks(
        self,
        candidate_page: int,
        candidate_size: int,
        keywords: list[str],
        rewritten_query: str,
        *,
        chunks_path: str | None = None,
        law_names: list[str] | None = None,
        court_names: list[str] | None = None,
        case_numbers: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        url = f"{self.base_url}{chunks_path or self.chunks_path}"
        print(f"request url = {url}")
        params: dict[str, Any] = {
            "page": candidate_page,
            "size": candidate_size,
            "keywords": keywords,
            "query": rewritten_query,
            "sort": "relevance,desc",
        }
        if law_names:
            params["lawNames"] = law_names
        if court_names:
            params["courtNames"] = court_names
        if case_numbers:
            params["caseNumbers"] = case_numbers

        cache_key = (
            url,
            candidate_page,
            candidate_size,
            tuple(keywords),
            rewritten_query,
            tuple(law_names or ()),
            tuple(court_names or ()),
            tuple(case_numbers or ()),
        )
        cached = await self._get_cached(cache_key)
        if cached is not None:
            print(f"[SEARCH_CACHE] hit url={url}", flush=True)
            return cached

        print(f"[SEARCH_CACHE] miss url={url}", flush=True)
        request = self._client.build_request("GET", url, params=params)

        print("========== SPRING REQUEST ==========")
        print(request.url)
        print("========== SPRING REQUEST END ==========")

        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        raw_chunks = self._extract_chunk_list(data)
        pagination = self._extract_pagination(
            data,
            requested_page=candidate_page,
            requested_size=candidate_size,
        )
        if raw_chunks is None:
            result = ([], pagination)
        else:
            result = (
                [item for item in raw_chunks if isinstance(item, dict)],
                pagination,
            )
        await self._set_cached(cache_key, result)
        return result

    @staticmethod
    def _extract_chunk_list(data: Any) -> list[Any] | None:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return None

        # Spring style: ApiResponse<ChunkResponse> -> {"data": {...}}
        wrapped_data = data.get("data")
        if isinstance(wrapped_data, dict):
            for key in ("chunks", "content", "items", "list"):
                candidate = wrapped_data.get(key)
                if isinstance(candidate, list):
                    return candidate

        # Fallback for direct response shapes.
        for key in ("chunks", "content", "items", "list"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return candidate
        return None

    @staticmethod
    def _extract_pagination(data: Any, requested_page: int, requested_size: int) -> dict[str, Any]:
        wrapped = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
        source = wrapped if isinstance(wrapped, dict) else {}

        zero_based_page = source.get("number")
        one_based_page = source.get("page") or source.get("currentPage")

        page_value = requested_page
        if isinstance(one_based_page, int):
            page_value = max(one_based_page, 1)
        elif isinstance(zero_based_page, int):
            page_value = zero_based_page + 1

        total_pages = source.get("totalPages") or source.get("total_pages")
        has_next = source.get("hasNext") or source.get("has_next")
        if has_next is None and isinstance(total_pages, int) and isinstance(page_value, int):
            has_next = page_value < total_pages

        return {
            "page": page_value,
            "size": int(source.get("size", requested_size)),
            "total_elements": source.get("totalElements") or source.get("total_elements"),
            "total_pages": total_pages,
            "has_next": has_next,
            "has_previous": source.get("hasPrevious") or source.get("has_previous"),
        }
