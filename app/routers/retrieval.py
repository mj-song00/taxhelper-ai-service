from functools import lru_cache
from time import perf_counter
from uuid import uuid4

import httpx

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.schemas.retrieval import (
    ChatRequest,
    ChatResponse,
)
from app.services.chunk_client import ChunkSearchClient
from app.services.chunk_search_service import ChunkSearchService
from app.services.llm_service import LlmService
from app.services.precedent_search_service import PrecedentSearchService

router = APIRouter(tags=["retrieval"])


def log_chat_timing(
    request_id: str,
    phase: str,
    started_at: float,
    **details: object,
) -> None:
    elapsed_sec = perf_counter() - started_at
    detail_text = " ".join(f"{key}={value}" for key, value in details.items())
    suffix = f" {detail_text}" if detail_text else ""
    print(
        f"[CHAT_TIMING] request_id={request_id} phase={phase} "
        f"elapsed_sec={elapsed_sec:.3f}{suffix}",
        flush=True,
    )


@lru_cache
def get_chunk_search_service() -> ChunkSearchService:
    settings = get_settings()
    client = ChunkSearchClient(
        base_url=settings.spring_base_url,
        chunks_path=settings.spring_chunks_path,
        precedent_chunks_path=settings.spring_precedent_chunks_path,
        timeout_sec=settings.request_timeout_sec,
        cache_ttl_sec=settings.search_cache_ttl_sec,
        cache_max_entries=settings.search_cache_max_entries,
    )
    return ChunkSearchService(
        client=client,
        candidate_size=settings.default_candidate_size,
    )


@lru_cache
def get_precedent_search_service() -> PrecedentSearchService:
    settings = get_settings()
    client = ChunkSearchClient(
        base_url=settings.spring_base_url,
        chunks_path=settings.spring_chunks_path,
        precedent_chunks_path=settings.spring_precedent_chunks_path,
        timeout_sec=settings.request_timeout_sec,
        cache_ttl_sec=settings.search_cache_ttl_sec,
        cache_max_entries=settings.search_cache_max_entries,
    )
    return PrecedentSearchService(
        client=client,
        candidate_size=settings.default_candidate_size,
    )


@lru_cache
def get_llm_service() -> LlmService:
    return LlmService()


@router.post("/chat", response_model=ChatResponse)
async def chat_with_llm(
    request: ChatRequest,
    http_request: Request,
    law_service: ChunkSearchService = Depends(get_chunk_search_service),
    precedent_service: PrecedentSearchService = Depends(get_precedent_search_service),
    llm_service: LlmService = Depends(get_llm_service),
) -> ChatResponse:
    request_id = getattr(http_request.state, "request_id", uuid4().hex[:8])
    total_started_at = perf_counter()
    print(
        f"[CHAT_TIMING] request_id={request_id} phase=request_started "
        f"question_chars={len(request.question)} top_k={request.top_k}",
        flush=True,
    )

    law_search_conditions = law_service.build_search_conditions(request.question)
    precedent_search_conditions = precedent_service.build_search_conditions(request.question)

    try:
        phase_started_at = perf_counter()
        law_chunks, _ = await law_service.retrieve_chunks(
            conditions=law_search_conditions,
            top_k=request.top_k,
        )
        log_chat_timing(
            request_id,
            "law_search_completed",
            phase_started_at,
            chunk_count=len(law_chunks),
        )

        if precedent_service.should_search(request.question):
            phase_started_at = perf_counter()
            precedent_chunks, _ = await precedent_service.retrieve_chunks(
                conditions=precedent_search_conditions,
                top_k=min(request.top_k, 3),
            )
            log_chat_timing(
                request_id,
                "precedent_search_completed",
                phase_started_at,
                chunk_count=len(precedent_chunks),
            )
        else:
            precedent_chunks = []
            log_chat_timing(
                request_id,
                "precedent_search_skipped",
                perf_counter(),
                chunk_count=0,
            )

    except httpx.HTTPStatusError as exc:
        log_chat_timing(
            request_id,
            "chunk_search_failed",
            total_started_at,
            status_code=exc.response.status_code,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Chunk search upstream error: {exc.response.status_code}",
        ) from exc

    except httpx.HTTPError as exc:
        log_chat_timing(request_id, "chunk_search_failed", total_started_at)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chunk search service is unavailable.",
        ) from exc

    context = (
        "[법령 근거]\n"
        f"{law_service.build_prompt_context(law_chunks, keywords=law_search_conditions['keywords'])}\n\n"
        "[판례 근거]\n"
        f"{precedent_service.build_prompt_context(precedent_chunks) or '검색된 판례 근거가 없습니다.'}"
    ).strip()

    try:
        phase_started_at = perf_counter()
        answer = await llm_service.generate_answer(
            question=request.question,
            context=context,
        )
        log_chat_timing(
            request_id,
            "llm_completed",
            phase_started_at,
            answer_chars=len(answer),
        )

    except httpx.HTTPStatusError as exc:
        log_chat_timing(
            request_id,
            "llm_failed",
            total_started_at,
            status_code=exc.response.status_code,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM upstream error: {exc.response.status_code}",
        ) from exc

    except httpx.HTTPError as exc:
        log_chat_timing(request_id, "llm_failed", total_started_at)
        print("========== Ollama 호출 실패 ==========")
        print("에러 타입:", type(exc))
        print("에러 내용:", str(exc))
        print("========== Ollama 호출 실패 끝 ==========")

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    response = ChatResponse(
        question=request.question,
        answer=answer,
        law_chunks=law_chunks,
        precedent_chunks=precedent_chunks,
        chunks=[*law_chunks, *precedent_chunks],
    )
    log_chat_timing(
        request_id,
        "request_completed",
        total_started_at,
        law_chunk_count=len(law_chunks),
        precedent_chunk_count=len(precedent_chunks),
    )
    return response
