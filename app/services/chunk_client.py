from __future__ import annotations

from typing import Any

import httpx


class ChunkSearchClient:
    def __init__(self, base_url: str, chunks_path: str, timeout_sec: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.chunks_path = chunks_path
        self.timeout_sec = timeout_sec

    async def fetch_chunks(
        self,
        candidate_page: int,
        candidate_size: int,
        law_names: list[str],
        keywords: list[str],
        rewritten_query: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        url = f"{self.base_url}{self.chunks_path}"
        params = {
            "page": candidate_page,
            "size": candidate_size,
            "lawNames": law_names,
            "keywords": keywords,
            "query": rewritten_query,
            "sort": "relevance,desc",
        }
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        raw_chunks = self._extract_chunk_list(data)
        pagination = self._extract_pagination(
            data,
            requested_page=candidate_page,
            requested_size=candidate_size,
        )
        if raw_chunks is None:
            return [], pagination
        return [item for item in raw_chunks if isinstance(item, dict)], pagination

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
        one_based_page = source.get("page")

        page_value = requested_page
        if isinstance(one_based_page, int):
            page_value = max(one_based_page, 1)
        elif isinstance(zero_based_page, int):
            page_value = zero_based_page + 1

        return {
            "page": page_value,
            "size": int(source.get("size", requested_size)),
            "total_elements": source.get("totalElements") or source.get("total_elements"),
            "total_pages": source.get("totalPages") or source.get("total_pages"),
            "has_next": source.get("hasNext") or source.get("has_next"),
            "has_previous": source.get("hasPrevious") or source.get("has_previous"),
        }
