from functools import lru_cache

import httpx

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import get_settings
from app.schemas.retrieval import (
    ChatRequest,
    ChatResponse,
)
from app.services.chunk_client import ChunkSearchClient
from app.services.chunk_search_service import ChunkSearchService
from app.services.llm_service import LlmService

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


@lru_cache
def get_llm_service() -> LlmService:
    return LlmService()


@router.post("/chat", response_model=ChatResponse)
async def chat_with_llm(
    request: ChatRequest,
    service: ChunkSearchService = Depends(get_chunk_search_service),
    llm_service: LlmService = Depends(get_llm_service),
) -> ChatResponse:
    law_search_conditions = service.build_search_conditions(request.question)

    try:
        law_chunks, _ = await service.retrieve_chunks(
            conditions=law_search_conditions,
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

    context = (
        "[법령 근거]\n"
        f"{service.build_prompt_context(law_chunks)}"
    ).strip()

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
        chunks=law_chunks,
    )
