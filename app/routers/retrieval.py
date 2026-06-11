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


@router.post("/chat", response_model=ChatResponse)
async def chat_with_llm(
    request: ChatRequest,
    service: ChunkSearchService = Depends(get_chunk_search_service),
    precedent_service: PrecedentSearchService = Depends(get_precedent_search_service),
    llm_service: LlmService = Depends(get_llm_service),
) -> ChatResponse:
    law_search_conditions = service.build_search_conditions(request.question)
    precedent_search_conditions = precedent_service.build_search_conditions(request.question)
    
    precedent_chunks = []
    
    try:
        law_chunks, _ = await service.retrieve_chunks(
        conditions=law_search_conditions,
        top_k=3,
        )

        # precedent_chunks, _ = await precedent_service.retrieve_chunks(
        #     conditions=precedent_search_conditions,
        #     top_k=2,
        # )

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

    law_context = service.build_prompt_context(law_chunks)
    precedent_context = precedent_service.build_prompt_context(precedent_chunks)

    context = (
        "[법령 근거]\n"
        f"{law_context}\n\n"
        "[판례 근거]\n"
        f"{precedent_context}"
    ).strip()

    chunks = law_chunks + precedent_chunks

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
        print("========== Ollama 호출 실패 ==========")
        print("에러 타입:", type(exc))
        print("에러 내용:", str(exc))
        print("========== Ollama 호출 실패 끝 ==========")

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return ChatResponse(
        question=request.question,
        answer=answer,
        chunks=chunks,
    )