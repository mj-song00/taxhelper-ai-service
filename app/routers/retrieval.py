from functools import lru_cache
import json
import re
from time import perf_counter
from uuid import uuid4

import httpx

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.schemas.retrieval import (
    ChatRequest,
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


def encode_sse(event: str, data: object) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def compact_chunk(
    chunk,
    max_content_chars: int = 500,
    content_preview: str | None = None,
) -> dict:
    data = chunk.model_dump(mode="json")
    original_content = str(data.get("content") or "")
    content = content_preview if content_preview is not None else original_content
    preview = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    preview = re.sub(r"<[^>]+>", " ", preview)
    preview = re.sub(r"\s+", " ", preview).strip()
    truncated = content_preview is not None or len(preview) > max_content_chars
    data["content"] = (
        f"{preview[:max_content_chars].rstrip()}…" if truncated else preview
    )
    data["content_truncated"] = truncated
    return data


@router.post(
    "/chat",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Token stream. Events: metadata, token, done.",
        }
    },
)
async def chat_with_llm(
    request: ChatRequest,
    http_request: Request,
    law_service: ChunkSearchService = Depends(get_chunk_search_service),
    precedent_service: PrecedentSearchService = Depends(get_precedent_search_service),
    llm_service: LlmService = Depends(get_llm_service),
) -> StreamingResponse:
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

    if precedent_chunks:
        # 판례 질문에서는 판례 근거가 전역 컨텍스트 제한에 잘리지 않도록
        # 판례를 먼저 배치하고 법령은 가장 관련도 높은 한 건만 보조한다.
        precedent_context = precedent_service.build_prompt_context(
            precedent_chunks[:2],
            keywords=precedent_search_conditions["keywords"],
            max_content_chars=950,
        )
        law_context = law_service.build_prompt_context(
            law_chunks[:1],
            max_content_chars=450,
            keywords=law_search_conditions["keywords"],
            keyword_weights=law_search_conditions.get("keyword_weights"),
        )
        context = (
            f"[판례 근거]\n{precedent_context}\n\n"
            f"[법령 근거]\n{law_context}"
        ).strip()
    else:
        context = (
            "[법령 근거]\n"
            f"{law_service.build_prompt_context(law_chunks, keywords=law_search_conditions['keywords'], keyword_weights=law_search_conditions.get('keyword_weights'))}\n\n"
            "[판례 근거]\n검색된 판례 근거가 없습니다."
        ).strip()

    law_chunk_data = [compact_chunk(chunk) for chunk in law_chunks]
    precedent_chunk_data = [
        compact_chunk(
            chunk,
            content_preview=precedent_service.extract_relevant_excerpt(
                chunk.content,
                precedent_search_conditions["keywords"],
                500,
            ),
        )
        for chunk in precedent_chunks
    ]

    async def event_stream():
        answer_parts: list[str] = []
        llm_started_at = perf_counter()

        yield encode_sse(
            "metadata",
            {
                "request_id": request_id,
                "question": request.question,
                "law_chunk_count": len(law_chunk_data),
                "precedent_chunk_count": len(precedent_chunk_data),
            },
        )

        try:
            async for piece in llm_service.stream_answer(
                question=request.question,
                context=context,
            ):
                answer_parts.append(piece)
                yield encode_sse("token", {"content": piece})
        except Exception as exc:
            log_chat_timing(request_id, "llm_failed", llm_started_at)
            print(
                f"[CHAT_STREAM_ERROR] request_id={request_id} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
            yield encode_sse(
                "error",
                {"message": "답변 생성 중 오류가 발생했습니다."},
            )
            return

        current_answer = "".join(answer_parts)
        negative_answer_markers = (
            "판례가 없습니다",
            "판단할 수 없습니다",
            "근거가 없습니다",
        )
        if precedent_chunks and not any(
            marker in current_answer for marker in negative_answer_markers
        ):
            primary_metadata = precedent_chunks[0].metadata
            case_number = str(primary_metadata.get("caseNumber") or "").strip()
            if case_number and case_number not in current_answer:
                citation_values = [
                    primary_metadata.get("courtName"),
                    primary_metadata.get("sentencingDate"),
                    case_number,
                    primary_metadata.get("caseName"),
                ]
                citation = " / ".join(
                    str(value) for value in citation_values if value
                )
                citation_piece = f"\n\n검색 근거: {citation}"
                answer_parts.append(citation_piece)
                yield encode_sse("token", {"content": citation_piece})

        answer = "".join(answer_parts).strip()
        log_chat_timing(
            request_id,
            "llm_completed",
            llm_started_at,
            answer_chars=len(answer),
        )
        yield encode_sse(
            "done",
            {
                "question": request.question,
                "answer": answer,
                "law_chunks": law_chunk_data,
                "precedent_chunks": precedent_chunk_data,
                "chunks": [
                    *(
                        {"chunk_id": chunk["chunk_id"], "source_type": "law"}
                        for chunk in law_chunk_data
                    ),
                    *(
                        {"chunk_id": chunk["chunk_id"], "source_type": "precedent"}
                        for chunk in precedent_chunk_data
                    ),
                ],
            },
        )
        log_chat_timing(
            request_id,
            "request_completed",
            total_started_at,
            law_chunk_count=len(law_chunks),
            precedent_chunk_count=len(precedent_chunks),
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
