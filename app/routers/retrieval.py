from functools import lru_cache

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import get_settings
from app.schemas.retrieval import RetrieveRequest, RetrieveResponse, SearchConditions
from app.services.chunk_client import ChunkSearchClient
from app.services.chunk_search_service import ChunkSearchService

router = APIRouter(tags=["retrieval"])


@lru_cache
def get_chunk_search_service() -> ChunkSearchService:
    settings = get_settings()
    client = ChunkSearchClient(
        base_url=settings.spring_base_url,
        chunks_path=settings.spring_chunks_path,
        timeout_sec=settings.request_timeout_sec,
    )
    return ChunkSearchService(
        client=client,
        candidate_size=settings.default_candidate_size,
    )


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve_law_chunks(
    request: RetrieveRequest,
    service: ChunkSearchService = Depends(get_chunk_search_service),
) -> RetrieveResponse:
    top_k = request.top_k
    search_conditions = service.build_search_conditions(request.question)
    search_prompt = service.build_search_prompt(
        question=request.question,
        conditions=search_conditions,
    )

    try:
        chunks, candidate_pagination = await service.retrieve_chunks(
            conditions=search_conditions,
            top_k=top_k,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Chunk search upstream error: {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chunk search service is unavailable.",
        ) from exc

    return RetrieveResponse(
        question=request.question,
        search_prompt=search_prompt,
        search_conditions=SearchConditions(**search_conditions),
        candidate_pagination=candidate_pagination,
        chunks=chunks,
        prompt_context=service.build_prompt_context(chunks),
    )
