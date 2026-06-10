from functools import lru_cache

import httpx
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import get_settings
from app.schemas.retrieval import (
    ChatRequest,
    ChatResponse,
    PrecedentRetrieveResponse,
    PrecedentSearchConditions,
    RetrieveRequest,
    RetrieveResponse,
    SearchConditions,
)
from app.services.chunk_client import ChunkSearchClient
from app.services.chunk_search_service import ChunkSearchService
from app.services.llm_service import LlmService
from app.services.precedent_search_service import PrecedentSearchService

router = APIRouter(tags=["retrieval"])


@lru_cache
def get_precedent_search_service() -> PrecedentSearchService:
    settings = get_settings()
    client = ChunkSearchClient(
        base_url=settings.spring_base_url,
        chunks_path=settings.spring_precedent_chunks_path,
        timeout_sec=settings.request_timeout_sec,
    )
    return PrecedentSearchService(
        client=client,
        candidate_size=settings.default_candidate_size,
    )


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


@lru_cache
def get_llm_service() -> LlmService:
    return LlmService()


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


@router.post("/retrieve/precedents", response_model=PrecedentRetrieveResponse)
async def retrieve_precedent_chunks(
    request: RetrieveRequest,
    service: PrecedentSearchService = Depends(get_precedent_search_service),
) -> PrecedentRetrieveResponse:
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
            detail=f"Precedent chunk search upstream error: {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Precedent chunk search service is unavailable.",
        ) from exc

    return PrecedentRetrieveResponse(
        question=request.question,
        search_prompt=search_prompt,
        search_conditions=PrecedentSearchConditions(**search_conditions),
        candidate_pagination=candidate_pagination,
        chunks=chunks,
        prompt_context=service.build_prompt_context(chunks),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat_with_llm(
    request: ChatRequest,
    service: ChunkSearchService = Depends(get_chunk_search_service),
    llm_service: LlmService = Depends(get_llm_service),
) -> ChatResponse:
    search_conditions = service.build_search_conditions(request.question)

    try:
        chunks, _ = await service.retrieve_chunks(
            conditions=search_conditions,
            top_k=request.top_k,
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

    context = service.build_prompt_context(chunks)

    try:
        answer = await llm_service.generate_answer(
            question=request.question,
            context=context,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM upstream error: {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM service is unavailable.",
        ) from exc

    return ChatResponse(
        question=request.question,
        answer=answer,
        chunks=chunks,
    )