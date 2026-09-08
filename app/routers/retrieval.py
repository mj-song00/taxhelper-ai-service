from dataclasses import dataclass
from functools import lru_cache
import json
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.schemas.retrieval import (
    ChatJobRequest,
    ChatJobResponse,
    ChatRequest,
    ChatResponse,
)
from app.services.chat_job_client import ChatJobClient
from app.services.chunk_client import ChunkSearchClient
from app.services.chunk_search_service import ChunkSearchService
from app.services.llm_service import LlmService
from app.services.precedent_search_service import PrecedentSearchService
from app.services.rabbitmq_publisher import RabbitMQPublisher

router = APIRouter(tags=["retrieval"])

NOMINEE_BUSINESS_BASIS = (
    "국세기본법 제14조제1항(실질과세): 과세의 대상이 되는 소득, 수익, "
    "재산, 행위 또는 거래의 귀속이 명의일 뿐이고 사실상 귀속되는 자가 "
    "따로 있을 때에는 사실상 귀속되는 자를 납세의무자로 하여 세법을 적용한다."
)


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


@lru_cache
def get_chat_job_client() -> ChatJobClient:
    settings = get_settings()
    return ChatJobClient(
        base_url=settings.spring_base_url,
        jobs_path=settings.spring_chat_jobs_path,
        timeout_sec=settings.request_timeout_sec,
    )


@lru_cache
def get_rabbitmq_publisher() -> RabbitMQPublisher:
    settings = get_settings()
    return RabbitMQPublisher(
        url=settings.rabbitmq_url,
        queue_name=settings.rabbitmq_queue,
    )


def encode_sse(event: str, data: object) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@router.post(
    "/rabbitmq/test-publish",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Publish a RabbitMQ connection test message",
)
async def publish_rabbitmq_test() -> dict[str, str]:
    job_id = str(uuid4())
    try:
        await get_rabbitmq_publisher().publish(
            {
                "job_id": job_id,
                "message": "rabbitmq connection test",
            }
        )
    except Exception as exc:
        print(
            f"[RABBITMQ] status=publish_failed job_id={job_id} "
            f"error={type(exc).__name__}: {exc}",
            flush=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RabbitMQ publisher is unavailable.",
        ) from exc

    return {"job_id": job_id, "status": "published"}


@router.post(
    "/chat/jobs",
    response_model=ChatJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Prepare and enqueue a chat job",
)
async def create_chat_job(
    request: ChatJobRequest,
    http_request: Request,
    law_service: ChunkSearchService = Depends(get_chunk_search_service),
    precedent_service: PrecedentSearchService = Depends(get_precedent_search_service),
    chat_job_client: ChatJobClient = Depends(get_chat_job_client),
    rabbitmq_publisher: RabbitMQPublisher = Depends(get_rabbitmq_publisher),
) -> ChatJobResponse:
    chat_request = ChatRequest(question=request.question, top_k=request.top_k)
    request_id, total_started_at = start_chat_request(http_request, chat_request)
    failure_message = "Chat job preparation failed."

    try:
        prepared = await prepare_chat(
            chat_request,
            request_id,
            total_started_at,
            law_service,
            precedent_service,
        )
        sources = build_sources(
            prepared.law_chunk_data,
            prepared.precedent_chunk_data,
        )

        failure_message = "Prepared chat data storage failed."
        await chat_job_client.save_prepared(
            request.job_id,
            prepared.context,
            sources,
        )
        print(
            f"[CHAT_JOB] job_id={request.job_id} phase=prepared",
            flush=True,
        )

        failure_message = "RabbitMQ publish failed."
        await rabbitmq_publisher.publish({"job_id": str(request.job_id)})

        failure_message = "Chat job WAITING update failed."
        await chat_job_client.mark_waiting(request.job_id)
        print(
            f"[CHAT_JOB] job_id={request.job_id} status=WAITING",
            flush=True,
        )
    except Exception as exc:
        try:
            await chat_job_client.mark_failed(request.job_id, failure_message)
        except Exception as status_exc:
            print(
                f"[CHAT_JOB] job_id={request.job_id} "
                f"status=failed_update_error error={type(status_exc).__name__}",
                flush=True,
            )
        print(
            f"[CHAT_JOB] job_id={request.job_id} status=FAILED "
            f"error={type(exc).__name__}",
            flush=True,
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=failure_message,
        ) from exc

    return ChatJobResponse(job_id=request.job_id, status="WAITING")


def compact_chunk(
    chunk,
    max_content_chars: int = 500,
    content_preview: str | None = None,
) -> dict:
    data = chunk.model_dump(mode="json")
    original_content = str(data.get("content") or "")
    content = content_preview if content_preview is not None else original_content
    original_preview = re.sub(
        r"<br\s*/?>",
        "\n",
        original_content,
        flags=re.IGNORECASE,
    )
    original_preview = re.sub(r"<[^>]+>", " ", original_preview)
    original_preview = re.sub(r"\s+", " ", original_preview).strip()
    preview = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    preview = re.sub(r"<[^>]+>", " ", preview)
    preview = re.sub(r"\s+", " ", preview).strip()
    response_truncated = len(preview) > max_content_chars
    source_truncated = (
        content_preview is not None
        and preview != original_preview
    )
    data["content"] = (
        f"{preview[:max_content_chars].rstrip()}…"
        if response_truncated
        else preview
    )
    data["content_truncated"] = source_truncated or response_truncated
    return data


def build_sources(
    law_chunks: list[dict],
    precedent_chunks: list[dict],
) -> list[dict]:
    sources: list[dict] = []
    seen_precedent_keys: set[str] = set()

    for chunk in law_chunks:
        sources.append(
            {
                "source_type": "law",
                "chunk_id": chunk["chunk_id"],
                "law_name": chunk.get("law_name"),
                "title": chunk.get("title") or chunk.get("article"),
                "excerpt": chunk.get("content") or "",
                "content_truncated": bool(chunk.get("content_truncated")),
                "full_content_available": bool(chunk.get("content_truncated")),
                "score": chunk.get("score"),
            }
        )

    for chunk in precedent_chunks:
        metadata = chunk.get("metadata") or {}
        case_number = str(metadata.get("caseNumber") or "").strip()
        precedent_id = str(chunk.get("precedent_id") or "").strip()
        # A judgment can be stored more than once or split into multiple
        # chunks (issue/summary/full text). Keep all chunks in the LLM context,
        # but expose only one source card per case to the user.
        precedent_key = case_number or precedent_id
        if precedent_key and precedent_key in seen_precedent_keys:
            continue
        if precedent_key:
            seen_precedent_keys.add(precedent_key)
        sources.append(
            {
                "source_type": "precedent",
                "chunk_id": chunk["chunk_id"],
                "precedent_id": chunk.get("precedent_id"),
                "title": chunk.get("title"),
                "court_name": metadata.get("courtName"),
                "case_number": case_number or None,
                "case_name": metadata.get("caseName"),
                "sentencing_date": metadata.get("sentencingDate"),
                "case_conclusion_hint": infer_precedent_conclusion_hint(
                    chunk.get("content") or "",
                ),
                "excerpt": chunk.get("content") or "",
                "content_truncated": bool(chunk.get("content_truncated")),
                "full_content_available": bool(chunk.get("content_truncated")),
                "score": chunk.get("score"),
            }
        )

    return sources


def infer_precedent_conclusion_hint(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text or "")
    if "조세조약적용을부인할수있는지여부(적극)" in compact:
        return "조약 혜택 부인 가능 / 과세 가능"
    if "명의에따른조세조약적용을부인" in compact and "과세한다" in compact:
        return "조약 혜택 부인 가능 / 과세 가능"
    if "적용을부인할수있다" in compact and "과세한다" in compact:
        return "조약 혜택 부인 가능 / 과세 가능"
    if "과세처분은위법" in compact or "처분이위법" in compact:
        return "과세처분 위법"
    if "제한세율이적용" in compact or "제한세율을적용" in compact:
        return "조약 혜택 인정 / 제한세율 적용"
    return None


def select_answer_sources(answer: str, sources: list[dict]) -> list[dict]:
    """Keep only sources explicitly cited by the generated answer."""
    article_counts: dict[str, int] = {}
    for source in sources:
        if source.get("source_type") != "law":
            continue
        article_match = re.search(r"제\d+조(?:의\d+)?", source.get("title") or "")
        if article_match:
            article = article_match.group()
            article_counts[article] = article_counts.get(article, 0) + 1

    selected: list[dict] = []
    for source in sources:
        source_type = source.get("source_type")
        if source_type == "precedent":
            case_number = str(source.get("case_number") or "").strip()
            if case_number and case_number in answer:
                selected.append(source)
            continue

        if source_type != "law":
            continue

        law_name = str(source.get("law_name") or "").strip()
        title = str(source.get("title") or "").strip()
        article_match = re.search(r"제\d+조(?:의\d+)?", title)
        article = article_match.group() if article_match else ""
        if title and title in answer:
            selected.append(source)
        elif (
            article
            and article in answer
            and (law_name in answer or article_counts.get(article) == 1)
        ):
            selected.append(source)

    return selected


@dataclass
class PreparedChat:
    context: str
    law_chunk_data: list[dict]
    precedent_chunk_data: list[dict]
    law_chunks: list[Any]
    precedent_chunks: list[Any]


async def prepare_chat(
    request: ChatRequest,
    request_id: str,
    total_started_at: float,
    law_service: ChunkSearchService,
    precedent_service: PrecedentSearchService,
) -> PreparedChat:
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
                top_k=min(request.top_k, 5),
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
        context_precedent_chunks = precedent_chunks[:2]
        context_law_chunks = law_chunks[:1]
        # 판례 질문에서는 판례 근거가 전역 컨텍스트 제한에 잘리지 않도록
        # 판례를 먼저 배치하되 직접 기준 조문도 함께 제공한다.
        precedent_context = precedent_service.build_prompt_context(
            context_precedent_chunks,
            keywords=precedent_search_conditions["keywords"],
            max_content_chars=700,
        )
        law_context = law_service.build_prompt_context(
            context_law_chunks,
            max_content_chars=950,
            keywords=law_search_conditions["keywords"],
            keyword_weights=law_search_conditions.get("keyword_weights"),
        )
        context = (
            f"[판례 근거]\n{precedent_context}\n\n"
            f"[법령 근거]\n{law_context}"
        ).strip()
    else:
        context_precedent_chunks = []
        context_law_chunks = law_chunks
        context = (
            "[법령 근거]\n"
            f"{law_service.build_prompt_context(law_chunks, keywords=law_search_conditions['keywords'], keyword_weights=law_search_conditions.get('keyword_weights'))}\n\n"
            "[판례 근거]\n검색된 판례 근거가 없습니다."
        ).strip()

    # 응답에 표시하는 검색 근거와 실제 LLM 프롬프트 근거를 일치시킨다.
    law_chunk_data = [
        compact_chunk(
            chunk,
            max_content_chars=1000,
            content_preview=law_service.extract_relevant_excerpt(
                chunk.content,
                950,
                law_search_conditions["keywords"],
                law_search_conditions.get("keyword_weights"),
            ),
        )
        for chunk in context_law_chunks
    ]
    precedent_chunk_data = [
        compact_chunk(
            chunk,
            max_content_chars=1250,
            content_preview=precedent_service.extract_relevant_excerpt(
                chunk.content,
                precedent_search_conditions["keywords"],
                1200,
            ),
        )
        for chunk in context_precedent_chunks
    ]

    # The current local law corpus does not contain the National Tax Basic Act.
    # Supply the controlling provision for nominee-business questions so the
    # answer and the source shown to the user stay aligned.
    if law_service.is_nominee_business_question(request.question):
        context = f"[보충 법령 근거]\n{NOMINEE_BUSINESS_BASIS}\n\n{context}"
        law_chunk_data.insert(
            0,
            {
                "chunk_id": "curated-national-tax-basic-act-14",
                "law_name": "국세기본법",
                "title": "제14조 실질과세",
                "article": "제14조",
                "content": NOMINEE_BUSINESS_BASIS,
                "content_truncated": False,
                "score": None,
            },
        )

    return PreparedChat(
        context=context,
        law_chunk_data=law_chunk_data,
        precedent_chunk_data=precedent_chunk_data,
        law_chunks=law_chunks,
        precedent_chunks=context_precedent_chunks,
    )


def build_citation_suffix(answer: str, precedent_chunks: list[Any]) -> str:
    negative_answer_markers = (
        "판례가 없습니다",
        "판단할 수 없습니다",
        "근거가 없습니다",
    )
    if not precedent_chunks or any(
        marker in answer for marker in negative_answer_markers
    ):
        return ""

    cited_case_numbers = [
        str(chunk.metadata.get("caseNumber") or "").strip()
        for chunk in precedent_chunks
    ]
    if any(
        case_number and case_number in answer
        for case_number in cited_case_numbers
    ):
        return ""

    primary_metadata = precedent_chunks[0].metadata
    case_number = str(primary_metadata.get("caseNumber") or "").strip()
    if not case_number or case_number in answer:
        return ""

    citation_values = [
        primary_metadata.get("courtName"),
        primary_metadata.get("sentencingDate"),
        case_number,
        primary_metadata.get("caseName"),
    ]
    citation = " / ".join(str(value) for value in citation_values if value)
    return f"\n\n검색 근거: {citation}" if citation else ""


def build_chat_response(question: str, answer: str, prepared: PreparedChat) -> ChatResponse:
    all_sources = build_sources(
        prepared.law_chunk_data,
        prepared.precedent_chunk_data,
    )
    sources = select_answer_sources(
        answer,
        all_sources,
    )
    if not sources:
        sources = all_sources[: max(1, min(len(all_sources), 5))]
    return ChatResponse(
        question=question,
        answer=answer,
        sources=sources,
    )


def start_chat_request(http_request: Request, request: ChatRequest) -> tuple[str, float]:
    request_id = getattr(http_request.state, "request_id", uuid4().hex[:8])
    total_started_at = perf_counter()
    print(
        f"[CHAT_TIMING] request_id={request_id} phase=request_started "
        f"question_chars={len(request.question)} top_k={request.top_k}",
        flush=True,
    )
    return request_id, total_started_at


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Generate a complete chat answer",
)
async def chat_with_llm(
    request: ChatRequest,
    http_request: Request,
    law_service: ChunkSearchService = Depends(get_chunk_search_service),
    precedent_service: PrecedentSearchService = Depends(get_precedent_search_service),
    llm_service: LlmService = Depends(get_llm_service),
) -> ChatResponse:
    request_id, total_started_at = start_chat_request(http_request, request)
    prepared = await prepare_chat(
        request,
        request_id,
        total_started_at,
        law_service,
        precedent_service,
    )
    llm_started_at = perf_counter()
    answer = await llm_service.generate_answer(
        question=request.question,
        context=prepared.context,
    )
    answer = (answer + build_citation_suffix(answer, prepared.precedent_chunks)).strip()
    response = build_chat_response(request.question, answer, prepared)
    log_chat_timing(
        request_id,
        "request_completed",
        total_started_at,
        law_chunk_count=len(prepared.law_chunks),
        precedent_chunk_count=len(prepared.precedent_chunks),
    )
    log_chat_timing(
        request_id,
        "llm_completed",
        llm_started_at,
        answer_chars=len(answer),
    )
    return response


@router.post(
    "/chat/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Token stream. Events: metadata, token, done.",
        }
    },
    summary="Stream chat answer tokens",
)
async def stream_chat_with_llm(
    request: ChatRequest,
    http_request: Request,
    law_service: ChunkSearchService = Depends(get_chunk_search_service),
    precedent_service: PrecedentSearchService = Depends(get_precedent_search_service),
    llm_service: LlmService = Depends(get_llm_service),
) -> StreamingResponse:
    request_id, total_started_at = start_chat_request(http_request, request)
    prepared = await prepare_chat(
        request,
        request_id,
        total_started_at,
        law_service,
        precedent_service,
    )

    async def event_stream():
        answer_parts: list[str] = []
        llm_started_at = perf_counter()

        yield encode_sse(
            "metadata",
            {
                "request_id": request_id,
                "question": request.question,
                "law_chunk_count": len(prepared.law_chunk_data),
                "precedent_chunk_count": len(prepared.precedent_chunk_data),
            },
        )

        try:
            async for piece in llm_service.stream_answer(
                question=request.question,
                context=prepared.context,
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

        citation_piece = build_citation_suffix(
            "".join(answer_parts),
            prepared.precedent_chunks,
        )
        if citation_piece:
            answer_parts.append(citation_piece)
            yield encode_sse("token", {"content": citation_piece})

        answer = "".join(answer_parts).strip()
        response = build_chat_response(request.question, answer, prepared)
        log_chat_timing(
            request_id,
            "llm_completed",
            llm_started_at,
            answer_chars=len(answer),
        )
        yield encode_sse(
            "done",
            response.model_dump(mode="json"),
        )
        log_chat_timing(
            request_id,
            "request_completed",
            total_started_at,
            law_chunk_count=len(prepared.law_chunks),
            precedent_chunk_count=len(prepared.precedent_chunks),
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
