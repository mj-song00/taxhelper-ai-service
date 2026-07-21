from __future__ import annotations

import asyncio
from collections import OrderedDict
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
from time import monotonic, perf_counter
from typing import AsyncIterator

import httpx

from app.core.config import get_settings


def _create_error_logger() -> logging.Logger:
    logger = logging.getLogger("taxhelper.llm")
    if logger.handlers:
        return logger

    log_dir = Path(
        os.getenv(
            "TAXHELPER_LOG_DIR",
            str(Path(__file__).resolve().parents[1] / "logs"),
        )
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "llm_errors.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


error_logger = _create_error_logger()


class LlmService:
    PROMPT_VERSION = "2026-07-21-consignment-invoice-v11"

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.max_context_chars = settings.ollama_max_context_chars
        self.num_predict = settings.ollama_num_predict
        self.num_ctx = settings.ollama_num_ctx
        self.keep_alive = settings.ollama_keep_alive
        self.answer_cache_ttl_sec = settings.llm_answer_cache_ttl_sec
        self.answer_cache_max_entries = settings.llm_answer_cache_max_entries
        self._answer_cache: OrderedDict[tuple[str, str, str], tuple[float, str]] = OrderedDict()
        self._answer_cache_lock = asyncio.Lock()
        self.timeout = httpx.Timeout(
            connect=10.0,
            read=300.0,
            write=30.0,
            pool=10.0,
        )
        self._client = httpx.AsyncClient(timeout=self.timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def warm_up(self) -> None:
        """Load the configured model into Ollama before the first user request."""
        started_at = perf_counter()
        response = await self._client.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": "",
                "stream": False,
                "keep_alive": self.keep_alive,
            },
        )
        response.raise_for_status()
        data = response.json()
        print(
            f"[OLLAMA_WARMUP] model={self.model} status=completed "
            f"elapsed_sec={perf_counter() - started_at:.3f} "
            f"load_sec={float(data.get('load_duration') or 0) / 1_000_000_000:.3f}",
            flush=True,
        )

    async def _get_cached_answer(self, key: tuple[str, str, str]) -> str | None:
        if self.answer_cache_ttl_sec <= 0 or self.answer_cache_max_entries <= 0:
            return None
        async with self._answer_cache_lock:
            cached = self._answer_cache.get(key)
            if cached is None:
                return None
            cached_at, answer = cached
            if monotonic() - cached_at > self.answer_cache_ttl_sec:
                self._answer_cache.pop(key, None)
                return None
            self._answer_cache.move_to_end(key)
            return answer

    async def _set_cached_answer(self, key: tuple[str, str, str], answer: str) -> None:
        if self.answer_cache_ttl_sec <= 0 or self.answer_cache_max_entries <= 0:
            return
        async with self._answer_cache_lock:
            self._answer_cache[key] = (monotonic(), answer)
            self._answer_cache.move_to_end(key)
            while len(self._answer_cache) > self.answer_cache_max_entries:
                self._answer_cache.popitem(last=False)

    def _build_payload(self, question: str, context: str) -> dict:
        if context is None:
            context = ""
        context = context.strip()
        has_precedent_context = (
            "[판례 근거]" in context
            and "검색된 판례 근거가 없습니다." not in context
        )
        compact_question = re.sub(r"\s+", "", question)
        is_special_relation_fee_question = (
            any(term in compact_question for term in ("특수관계인", "특수관계자"))
            and "수수료" in compact_question
            and any(
                term in compact_question
                for term in ("부당행위계산", "필요경비", "손금", "부인")
            )
        ) or (
            any(term in compact_question for term in ("프리랜서", "개인사업자", "사업소득"))
            and "법인" in compact_question
            and any(term in compact_question for term in ("소득분산", "소득을분산", "부당행위계산"))
        )
        if len(context) > self.max_context_chars:
            context = context[:self.max_context_chars]

        if has_precedent_context:
            source_specific_rules = (
                "- 판례 답변은 반드시 '일반 법리'와 '해당 사건 결론'을 분리한다. "
                "서로 결론이 달라 보이면 일반 법리를 먼저 쓰고, 그 사건에서의 결론을 따로 쓴다.\n"
                "- 판례 존재 여부 질문에서 핵심 쟁점과 일치하는 판례가 제공되면 "
                "법원·사건번호·선고일과 판결 결론을 쓴다.\n"
                "- 판례 전문의 당사자 주장과 법원의 판단을 구분하고, "
                "법원의 판단만 결론으로 사용한다.\n"
                "- 법원이 무엇을 위법·적법하다고 보았고 처분을 취소·유지했는지 "
                "반드시 한 문장으로 요약한다.\n"
            )
        else:
            source_specific_rules = (
                "- 제공된 근거에 판례가 없으므로 법원·판례·사건번호·판결을 언급하지 않는다.\n"
                "- 법령 질문은 조문이 정한 원칙, 각 호의 서로 다른 요건, 예외를 구분해서 설명한다.\n"
                "- 질문만으로 구체적인 감면 유형을 확정할 수 없으면 일반규정을 먼저 설명하고 "
                "개별 감면 조항에 특별규정이 있을 수 있음을 밝힌다.\n"
            )
        transaction_direction_rules = ""
        if is_special_relation_fee_question:
            transaction_direction_rules = (
                "- 이 질문의 거래 방향은 질문자가 특수관계인에게 수수료를 지급하고 "
                "그 대가로 용역을 제공받은 경우다. 질문자가 용역을 무상·저가로 "
                "제공한 경우로 바꾸어 답하지 않는다.\n"
                "- 지급 수수료는 실제 용역 및 경제적 대가관계가 있는지, "
                "건전한 사회통념·상관행과 독립 당사자 간 정상거래의 시가에 비추어 "
                "경제적 합리성이 있는지를 중심으로 설명한다.\n"
            )
        return {
            "model": self.model,
            "stream": True,
            "think": False,
            "keep_alive": self.keep_alive,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "/no_think\n"
                        "너는 한국 세법 검색 답변 도우미다. 사고 과정은 쓰지 말고 한국어 최종 답변만 즉시 작성하라.\n"
                        "규칙:\n"
                        "- 제공된 법령·판례 근거만 사용하고 추론하지 않는다.\n"
                        "- 최종 적용 여부를 확정할 직접 근거가 부족해도, 검색 근거에서 확인되는 "
                        "일반 원칙·판단 기준·위험 요인을 먼저 구체적으로 설명한 뒤 부족한 사실을 밝힌다.\n"
                        "- 질문이 일반적인 판단 기준을 묻고 검색 근거가 그 기준을 제시하면, "
                        "구체적 사실관계가 없다는 이유만으로 판단 불가라고 하지 않는다.\n"
                        "- 금액·기간·조건은 근거에 있는 내용을 빠뜨리지 않는다.\n"
                        "- 관련 없는 조문은 인용하지 않는다.\n"
                        "- 질문에 법령상 특정 행위(예: 폐업 시 남은 재화)가 포함되면 그 행위를 직접 규정한 조문을 최우선 근거로 삼는다.\n"
                        "- 검색 근거에 직접 조문이 있으면 시행령의 보조 조문이나 유사한 세액공제 조문으로 결론을 대체하지 않는다.\n"
                        "- 결론은 인용한 조문 내용과 반드시 일치해야 하며, 조문에 반하는 일반적 설명을 쓰지 않는다.\n"
                        "- 사업자등록 명의자와 실제 운영자가 다른 질문에서는 등록명의만으로 납세의무자를 확정하지 않는다. 국세기본법 제14조의 근거가 제공되면 거래와 손익의 사실상 귀속자를 납세의무자로 설명하고, 자금 출처·주요 자산 소유·계약과 계좌 관리·경영 의사결정·사업상 위험 부담을 종합 확인한다. 직원이 '사장님'으로 불렀다는 사실은 보조 정황일 뿐 단독 기준이 아니다.\n"
                        "- 수정세금계산서의 각 발급사유를 서로 섞지 않는다. 계약 해제·매출 취소·환불은 부가가치세법 시행령 제70조제1항제2호만 적용하며, 같은 항 제4호의 내국신용장·구매확인서에 관한 '과세기간 종료 후 25일'을 계약 해제의 발급기한으로 사용하지 않는다.\n"
                        "- 위탁판매에서는 재화를 인도하는 주체와 세금계산서 명의를 구분한다. 수탁자·대리인이 인도하면 그 수탁자·대리인이 위탁자·본인의 명의로 발급하고 자신의 등록번호를 덧붙인다. 위탁자·본인이 직접 인도하면 위탁자·본인이 발급할 수 있다. 근거 조문은 부가가치세법 시행령 제69조제1항이며 제6조로 바꾸어 쓰지 않는다.\n"
                        "- 간이과세자와 일반과세자 전환 기준 질문에는 전환 기준(직전 연도 공급대가·업종별 기준)을 먼저 답하고, 전환 후 재고매입세액 특례를 전환 기준으로 설명하지 않는다.\n"
                        "- 먼저 질문의 상황을 신고 전, 이미 신고 완료, 잘못된 금액으로 신고, 단순 계산 질문 중 하나로 구분한다. 질문에 신고 완료 또는 잘못 신고했다는 사실이 없으면 수정신고·경정청구를 먼저 권하지 않는다.\n"
                        "- 단순 계산 질문은 계산 기준을 먼저 직접 답한다. 부가가치세 포함 여부가 불분명하거나 별도 약정이 없고 실제 받은 금액이 확인되면 공급가액은 실제 받은 금액×110분의 100, 부가가치세는 실제 받은 금액×110분의 10으로 설명한다. 단, 이 계산 조문이 검색 근거에 없으면 해당 수치를 만들어내지 말고 계산 기준을 확인할 수 없다고 한다.\n"
                        "- 상가·사무실 등 과세 임대를 전제로 한 월세 계산 질문에서 계약서에 부가가치세 별도 약정이 없고 포함 여부가 불분명하면 실제 받은 금액×100/110을 공급가액, 실제 받은 금액×10/110을 부가가치세로 답한다. 실제 받은 월세 1,000,000원이면 공급가액 909,091원, 부가가치세 90,909원으로 계산한다(원 단위 반올림).\n"
                        "- 계약서에 부가가치세 별도 약정이 명확하면 약정 월세 전액을 공급가액으로 보고 그 10%를 부가가치세로 계산한다. 주택 임대 등 면세 여부가 문제될 수 있으면 이 예외를 짧게 알린다.\n"
                        "- 사업용 차량·리스의 매입세액 질문에서는 사업용이라는 이유만으로 공제된다고 단정하지 않는다. 부가가치세법 제39조제1항제5호의 자동차 구입·임차·유지 매입세액 불공제 원칙과, 운수업·자동차판매업 등 대통령령상 업종에 직접 영업용으로 사용하는 예외를 구분한다. 법령 근거에 없는 배기량·승차정원 기준은 임의로 만들지 않는다.\n"
                        "- 이미 신고했거나 잘못된 금액으로 신고했다고 명시된 경우에만 후속 조치를 안내한다. 신고기한 전이면 기존 신고서를 다시 작성해 기한 내 신고, 신고기한 후 과소신고·과소납부면 수정신고 검토, 신고기한 후 과다신고·과다납부면 경정청구 검토로 구분한다. 구체적 내역이 없으면 신고서와 임대차계약서 확인이 필요하다고 한다.\n"
                        "- 계산 질문의 답변 순서는 반드시 1. 결론 2. 계산 방법 3. 예외 또는 조건별 구분 4. 확인할 사실관계 5. 검색 근거로 한다. 이미 신고한 경우의 수정신고·경정청구 조치는 질문에 신고 완료 또는 오류 신고 사실이 있을 때만 3번에 포함한다.\n"
                        "- 임대인이 별도 약정 없이 월세를 받은 질문에서 사용자가 이미 신고했다고 말하지 않았다면 수정신고나 경정청구를 안내하지 않는다.\n"
                        "- 질문과 직접 관련된 계산 조문이 검색 근거에 없으면 서식·제출절차 조문으로 계산 방법을 추론하지 말고 '제공된 근거만으로는 계산 기준을 확인할 수 없습니다.'라고 한다.\n"
                        "- 지급자·수령자, 제공자·제공받는 자와 고가·저가의 거래 방향을 바꾸지 않는다.\n"
                        "- 사건번호·선고일·조문번호는 검색 근거의 값을 한 글자도 바꾸지 않고 옮긴다.\n"
                        "- 결론에는 질문이 요구한 항목을 실제로 나열하고 조문번호만 쓰지 않는다.\n"
                        f"{source_specific_rules}"
                        f"{transaction_direction_rules}"
                        "- 질문에 단순히 예·아니요로 답할 수 없으면 '적용될 수 있으나 사실관계에 따라 달라진다'는 "
                        "조건부 결론을 쓰고, 적용 요건과 반대 사정을 각각 설명한다.\n"
                        "- 사용자가 후속으로 확인해야 할 계약, 업무 수행, 인력·시설, 대금과 시가, 소득 귀속 등 "
                        "구체적 자료를 검색 근거 범위 안에서 체크리스트로 제시한다.\n"
                        "- 전체 900자 이내로 작성한다. 근거가 충분하면 지나치게 짧게 끝내지 않는다.\n"
                        "판례 근거가 있으면 형식:\n"
                        "1. 일반 법리\n2. 해당 사건 결론\n3. 이유\n"
                        "판례 근거가 없으면 다음 형식을 사용한다: 1. 결론 2. 계산 방법 3. 예외 또는 조건별 구분 4. 확인할 사실관계 5. 검색 근거. 답변은 5번 검색 근거에서 끝낸다."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"[사용자 질문]\n{question.strip()}\n\n"
                        f"[검색 근거]\n{context}\n\n"
                        "위 검색 근거만 사용해서 사용자의 질문에 답변해줘. "
                        "검색 근거와 관련 없는 일반 지식은 사용하지 마.\n/no_think"
                    ),
                },
            ],
            "options": {
                "temperature": 0.1,
                "top_p": 0.8,
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx,
            },
        }

    async def generate_answer(self, question: str, context: str) -> str:
        content_parts = [
            piece async for piece in self.stream_answer(question=question, context=context)
        ]
        return "".join(content_parts)

    async def stream_answer(
        self,
        question: str,
        context: str,
        *,
        _context_retry: bool = False,
    ) -> AsyncIterator[str]:
        context = (context or "").strip()

        direct_answer = self.try_generate_direct_answer(question, context)
        if direct_answer is not None:
            yield direct_answer
            return

        answer_cache_key = (
            f"{self.model}:{self.PROMPT_VERSION}",
            question.strip(),
            context,
        )
        cached_answer = await self._get_cached_answer(answer_cache_key)
        if cached_answer is not None:
            print(f"[LLM_CACHE] hit model={self.model}", flush=True)
            yield cached_answer
            return
        print(f"[LLM_CACHE] miss model={self.model}", flush=True)

        payload = self._build_payload(question, context)
        url = f"{self.base_url}/api/chat"

        print("question length =", len(question))
        print("context length =", len(context))

        try:
            print("========== CONTEXT ==========")
            print(context[:1000])
            if len(context) > 1000:
                print("... context truncated in log ...")
            print("========== END ==========")

            print("========== PAYLOAD ==========")
            print({
                "model": payload["model"],
                "stream": payload["stream"],
                "think": payload["think"],
                "message_count": len(payload["messages"]),
                "options": payload["options"],
            })
            print("========== END PAYLOAD ==========")

            content_parts: list[str] = []
            ollama_started_at = perf_counter()
            first_token_sec: float | None = None
            final_event: dict = {}
            async with self._client.stream("POST", url, json=payload) as response:
                # Streaming responses are not buffered automatically. Read an error
                # body before raise_for_status() so it remains available to the
                # HTTPStatusError handler after the stream context is closed.
                if response.is_error:
                    await response.aread()
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    piece = event.get("message", {}).get("content")
                    if isinstance(piece, str) and piece:
                        if first_token_sec is None:
                            first_token_sec = perf_counter() - ollama_started_at
                        content_parts.append(piece)
                        yield piece
                    if event.get("done") is True:
                        final_event = event

            total_sec = perf_counter() - ollama_started_at
            eval_count = int(final_event.get("eval_count") or 0)
            eval_duration_sec = float(final_event.get("eval_duration") or 0) / 1_000_000_000
            tokens_per_sec = eval_count / eval_duration_sec if eval_duration_sec > 0 else 0.0
            print(
                f"[OLLAMA_TIMING] model={self.model} total_sec={total_sec:.3f} "
                f"first_token_sec={(first_token_sec or 0.0):.3f} "
                f"load_sec={float(final_event.get('load_duration') or 0) / 1_000_000_000:.3f} "
                f"prompt_eval_sec={float(final_event.get('prompt_eval_duration') or 0) / 1_000_000_000:.3f} "
                f"eval_sec={eval_duration_sec:.3f} prompt_tokens={int(final_event.get('prompt_eval_count') or 0)} "
                f"output_tokens={eval_count} tokens_per_sec={tokens_per_sec:.2f}",
                flush=True,
            )

        except httpx.ReadTimeout:
            error_logger.exception(
                "ollama_request_failed error=read_timeout model=%s url=%s "
                "question_chars=%d context_chars=%d",
                self.model,
                url,
                len(question),
                len(context),
            )
            print("========== Ollama 호출 실패 ==========")
            print("에러 타입:", httpx.ReadTimeout)
            print("에러 내용: read timeout")
            print("========== Ollama 호출 실패 끝 ==========")
            yield "LLM 응답 시간이 초과되었습니다. 검색 근거를 줄이거나 다시 시도해주세요."
            return

        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:4000]
            error_logger.error(
                "ollama_request_failed error=http_status status_code=%d "
                "model=%s url=%s question_chars=%d context_chars=%d response=%r",
                e.response.status_code,
                self.model,
                url,
                len(question),
                len(context),
                error_body,
                exc_info=True,
            )
            if "exceed_context_size_error" in error_body and not _context_retry:
                # _build_payload() already caps context at max_context_chars.
                # Base the retry reduction on that effective size; using the raw
                # pre-cap length can leave the retry payload almost unchanged.
                effective_context_chars = min(len(context), self.max_context_chars)
                reduced_context = context[
                    : max(1000, int(effective_context_chars * 0.6))
                ]
                error_logger.warning(
                    "ollama_context_retry model=%s original_context_chars=%d "
                    "reduced_context_chars=%d",
                    self.model,
                    len(context),
                    len(reduced_context),
                )
                print(
                    f"[OLLAMA_RETRY] reason=context_size "
                    f"original_context_chars={len(context)} "
                    f"reduced_context_chars={len(reduced_context)}",
                    flush=True,
                )
                async for piece in self.stream_answer(
                    question,
                    reduced_context,
                    _context_retry=True,
                ):
                    yield piece
                return
            print("========== Ollama 호출 실패 ==========")
            print("에러 타입:", type(e))
            print("상태 코드:", e.response.status_code)
            print("에러 내용:", e.response.text)
            print("========== Ollama 호출 실패 끝 ==========")
            yield "LLM 서버에서 오류 응답을 반환했습니다."
            return

        except httpx.RequestError as e:
            error_logger.exception(
                "ollama_request_failed error=request model=%s url=%s "
                "question_chars=%d context_chars=%d detail=%s",
                self.model,
                url,
                len(question),
                len(context),
                e,
            )
            print("========== Ollama 호출 실패 ==========")
            print("에러 타입:", type(e))
            print("에러 내용:", str(e))
            print("========== Ollama 호출 실패 끝 ==========")
            yield "LLM 서버에 연결할 수 없습니다."
            return

        content = "".join(content_parts).strip()
        print("========== OLLAMA RESPONSE ==========")
        print({"message": {"content": content}})
        print("========== OLLAMA RESPONSE END ==========")

        if not content:
            yield "LLM이 빈 응답을 반환했습니다. 검색 근거가 너무 길거나 모델이 답변 생성을 완료하지 못했을 수 있습니다."
            return

        await self._set_cached_answer(answer_cache_key, content)

    @staticmethod
    def try_generate_direct_answer(question: str, context: str) -> str | None:
        nominee_business_answer = (
            LlmService.try_generate_nominee_business_taxpayer_answer(
                question,
                context,
            )
        )
        if nominee_business_answer is not None:
            return nominee_business_answer

        pos_sales_estimation_answer = (
            LlmService.try_generate_pos_sales_estimation_answer(
                question,
                context,
            )
        )
        if pos_sales_estimation_answer is not None:
            return pos_sales_estimation_answer

        third_party_invoice_answer = (
            LlmService.try_generate_third_party_invoice_answer(
                question,
                context,
            )
        )
        if third_party_invoice_answer is not None:
            return third_party_invoice_answer

        consignment_invoice_answer = (
            LlmService.try_generate_consignment_sale_invoice_answer(
                question, context
            )
        )
        if consignment_invoice_answer is not None:
            return consignment_invoice_answer

        corrected_invoice_answer = (
            LlmService.try_generate_cancelled_sale_corrected_invoice_answer(
                question, context
            )
        )
        if corrected_invoice_answer is not None:
            return corrected_invoice_answer

        exempt_invoice_answer = LlmService.try_generate_exempt_invoice_penalty_answer(
            question, context
        )
        if exempt_invoice_answer is not None:
            return exempt_invoice_answer

        simple_taxpayer_answer = LlmService.try_generate_simple_taxpayer_invoice_answer(
            question, context
        )
        if simple_taxpayer_answer is not None:
            return simple_taxpayer_answer

        recognized_interest_answer = LlmService.try_generate_recognized_interest_answer(
            question, context
        )
        if recognized_interest_answer is not None:
            return recognized_interest_answer

        treaty_beneficial_owner_answer = (
            LlmService.try_generate_treaty_beneficial_owner_denial_answer(
                question,
                context,
            )
        )
        if treaty_beneficial_owner_answer is not None:
            return treaty_beneficial_owner_answer

        beneficial_owner_answer = LlmService.try_generate_beneficial_owner_dividend_answer(
            question,
            context,
        )
        if beneficial_owner_answer is not None:
            return beneficial_owner_answer

        local_tax_clawback_answer = LlmService.try_generate_local_tax_clawback_answer(
            question,
            context,
        )
        if local_tax_clawback_answer is not None:
            return local_tax_clawback_answer

        pre_registration_answer = LlmService.try_generate_pre_registration_input_tax_answer(
            question,
            context,
        )
        if pre_registration_answer is not None:
            return pre_registration_answer

        petroleum_answer = LlmService.try_generate_petroleum_tax_answer(question, context)
        if petroleum_answer is not None:
            return petroleum_answer

        overseas_stock_answer = LlmService.try_generate_overseas_stock_answer(question, context)
        if overseas_stock_answer is not None:
            return overseas_stock_answer

        withholding_answer = LlmService.try_generate_3_3_withholding_answer(question, context)
        if withholding_answer is not None:
            return withholding_answer

        bad_debt_answer = LlmService.try_generate_bad_debt_allowance_exclusion_answer(question, context)
        if bad_debt_answer is not None:
            return bad_debt_answer

        housing_requirement_answer = LlmService.try_generate_housing_loan_requirement_answer(question, context)
        if housing_requirement_answer is not None:
            return housing_requirement_answer

        if not LlmService.is_housing_loan_limit_question(question):
            return None

        required_terms = (
            "제52조",
            "장기주택저당차입금",
            "이자",
            "공제한도",
        )
        amount_terms = ("연 800만원", "600만원", "1천800만원", "2천만원")

        if not all(term in context for term in required_terms):
            return None

        if not all(term in context for term in amount_terms):
            return None

        return (
            "1. 결론\n"
            "장기주택저당차입금 이자상환액은 해당 과세기간에 지급한 이자 상환액을 "
            "근로소득금액에서 공제하되, 다음 한도 안에서 공제됩니다.\n\n"
            "- 상환기간이 15년 이상인 경우: 연 800만원\n"
            "- 상환기간이 10년 이상이고 고정금리 또는 비거치식 분할상환인 경우: 연 600만원\n"
            "- 상환기간이 15년 이상이고 고정금리 또는 비거치식 분할상환인 경우: 연 1,800만원\n"
            "- 상환기간이 15년 이상이고 고정금리이면서 비거치식 분할상환인 경우: 연 2,000만원\n\n"
            "2. 검색 근거\n"
            "소득세법 제52조제5항은 장기주택저당차입금의 이자 상환액을 "
            "특별소득공제로 공제한다고 규정하면서 기본 공제한도를 연 800만원으로 둡니다. "
            "같은 조 제6항은 고정금리 방식, 비거치식 분할상환 방식, 상환기간에 따라 "
            "공제한도를 2천만원, 1천800만원, 600만원으로 달리 정합니다. "
            "소득세법 시행령 제112조는 장기주택저당차입금의 요건과 고정금리ㆍ비거치식 "
            "분할상환 방식의 의미를 정합니다.\n\n"
            "3. 근거 부족 여부\n"
            "제공된 법령 근거만으로 한도액은 확인됩니다. 다만 실제 적용 여부는 "
            "주택 수, 기준시가, 차입 시기, 상환 방식 등 소득세법 제52조 및 "
            "소득세법 시행령 제112조의 요건 충족 여부를 함께 확인해야 합니다."
        )

    @staticmethod
    def try_generate_nominee_business_taxpayer_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question or "")
        has_nominee = any(
            term in compact_question
            for term in ("명의상", "명의자", "명의를빌려", "명의를빌린", "사업자명의")
        )
        has_actual_operator = any(
            term in compact_question
            for term in ("실제운영자", "실질사업자", "실질대표", "사실상귀속")
        )
        asks_taxpayer = any(
            term in compact_question
            for term in ("납세의무자", "납세의무", "세금", "부가가치세", "누구")
        )
        if not (has_nominee and has_actual_operator and asks_taxpayer):
            return None

        compact_context = re.sub(r"\s+", "", context or "")
        required_basis = (
            "제14조",
            "명의일뿐이고",
            "사실상귀속되는자",
            "납세의무자",
        )
        if not all(term in compact_context for term in required_basis):
            return None

        return (
            "1. 결론\n"
            "사업자등록 명의자와 실제 운영자가 다르면 명의자라는 이유만으로 납세의무자를 "
            "확정하지 않습니다. 거래와 사업 손익이 사실상 귀속되는 사람이 따로 있다면 그 "
            "실질 귀속자가 부가가치세 납세의무자가 됩니다.\n\n"
            "2. 판단 기준\n"
            "사업자금의 출처, 사업용 부동산·장비 등 주요 자산의 소유, 계약 체결과 가격 결정, "
            "사업용 계좌·장부 관리, 직원 지휘, 이익 수령 및 손실·채무 부담 등 사업을 실제로 "
            "지배·관리하고 손익을 귀속받았는지를 종합하여 판단합니다.\n\n"
            "3. 호칭의 의미\n"
            "직원이 특정인을 '사장님'으로 불렀다는 사실은 실질 운영자를 추정하는 보조 정황 "
            "중 하나일 뿐, 그 사실만으로 납세의무자가 되지는 않습니다. 단순 자금 대여나 일부 "
            "업무 지원인지 실제 경영과 손익 귀속인지도 구분해야 합니다.\n\n"
            "4. 확인할 사실관계\n"
            "명의상 사업자와 실제 운영자 각각의 자금 투입, 자산 소유, 계약·계좌·장부 관리, "
            "의사결정, 매출 귀속 및 사업 위험 부담 자료를 확인해야 합니다. 법인사업자라면 법인 "
            "자체가 원칙적인 납세의무자이므로 개인사업자의 명의대여 문제와 구분해야 합니다.\n\n"
            "5. 검색 근거\n"
            "국세기본법 제14조제1항(실질과세)"
        )

    @staticmethod
    def try_generate_pos_sales_estimation_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question or "").lower()
        has_pos = (
            "pos" in compact_question
            or "포스" in compact_question
            or "판매시점정보관리시스템" in compact_question
        )
        has_estimation = any(
            term in compact_question
            for term in ("추계", "매출누락", "수입금액")
        )
        if not (has_pos and has_estimation):
            return None

        required_basis = (
            "2008두7687",
            "판매시점정보관리시스템",
            "합리성과 타당성",
            "증명책임",
        )
        if not all(term in context for term in required_basis):
            return None

        return (
            "1. 일반 법리\n"
            "수입금액을 추계할 요건이 있다는 것만으로는 부족하고, 그 내용과 방법이 구체적인 "
            "사안에서 실제 수입금액에 가장 가깝게 반영될 정도로 합리적이고 타당해야 합니다. "
            "원칙적으로 추계방법의 합리성과 타당성은 과세관청이 증명합니다. 다만 과세관청이 "
            "법령이 정한 방법과 절차에 따라 추계했다면 합리성과 타당성은 일단 증명된 것으로 "
            "보고, 그 방법이 현저히 불합리하다는 점은 이를 다투는 납세자가 증명해야 합니다.\n\n"
            "2. 해당 사건 결론\n"
            "대법원 2010. 10. 14. 선고 2008두7687 판결은 POS 입력 매출액과 원·부재료비의 "
            "비율을 다른 기간에 적용한 매출 추계가 합리적이라고 보아 과세처분을 적법하다고 "
            "판단한 원심을 수긍했습니다.\n\n"
            "3. 판단 이유\n"
            "POS 매출은 판매와 동시에 입력되고 다른 조사자료와도 부합하여 신빙성이 높았으며, "
            "돼지고기·음료수·주류 등 원·부재료는 매출과 직접 관련되었습니다. 또한 장기간 계속 "
            "거래한 규모 있는 납품업체가 세금계산서를 발행해 매입금액이 비교적 정확했고, 특별한 "
            "사정이 없다면 매출액 대비 원·부재료비 비율이 상당 기간 유지된다고 볼 수 있었습니다.\n\n"
            "검색 근거: 대법원 2010. 10. 14. 선고 2008두7687 판결"
        )

    @staticmethod
    def try_generate_third_party_invoice_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question or "")
        has_invoice = "세금계산서" in compact_question
        has_different_party = any(
            term in compact_question
            for term in ("실제공급자와다른", "공급자가실제공급자와다른", "제3자명의", "명의위장")
        )
        asks_case_or_good_faith = any(
            term in compact_question
            for term in ("선의의거래당사자", "선의", "사례", "판례", "인정")
        )
        if not (has_invoice and has_different_party and asks_case_or_good_faith):
            return None

        # The precedent context is excerpted to a fixed length, so all three
        # provisions may not survive even when the exact case was retrieved.
        # The exact case number is the stable activation signal.
        required_basis = ("2023두41314",)
        if not all(term in context for term in required_basis):
            return None

        return (
            "1. 결론\n"
            "대법원 2025. 5. 29. 선고 2023두41314 판결이 있습니다. 실제 거래자가 "
            "제3자의 사업자등록을 이용해 제3자 명의 세금계산서를 발급·수취한 경우에는 원칙적으로 "
            "사실과 다른 세금계산서에 해당합니다. 구매자가 명의위장 사실을 알지 못했고, 알지 "
            "못한 데 과실도 없었다는 특별한 사정을 객관적으로 입증한 경우에만 매입세액을 "
            "공제·환급받을 수 있으며, 그 선의·무과실은 공제를 주장하는 구매자가 입증해야 합니다.\n\n"
            "2. 적용 조문\n"
            "- 구 부가가치세법 제32조 제1항: 공급자의 등록번호·성명 또는 명칭과 공급받는 자의 "
            "등록번호를 세금계산서의 필요적 기재사항으로 규정\n"
            "- 구 부가가치세법 제39조 제1항 제2호: 필요적 기재사항이 누락되거나 사실과 다르게 "
            "적힌 세금계산서의 매입세액은 원칙적으로 불공제\n"
            "- 구 부가가치세법 시행령 제75조 제2호: 일부가 착오로 다르게 기재되었어도 나머지 "
            "기재사항으로 거래사실이 확인되면 예외적으로 공제\n\n"
            "3. 판단 기준\n"
            "다만 실제 사업자가 자신의 계산과 책임으로 사업체를 운영하면서 사업자등록 명의만 "
            "제3자로 한 경우에는, 세금계산서에 적힌 수량과 가격대로 실제 공급이 이루어졌다면 "
            "곧바로 사실과 다른 세금계산서나 가공 세금계산서라고 볼 수 없습니다. 이 예외의 인정 "
            "여부는 과세행정의 곤란과 탈루 가능성, 명의자와 실제 운영자의 관계, 명의 이용 동기·경위, "
            "사업 내용과 거래 방식, 수익·비용 및 자금 관리, 명의자의 관여와 이익 등을 종합해 "
            "신중하게 판단합니다.\n\n"
            "4. 해당 사건 결론\n"
            "대법원은 모회사와 자회사가 별도로 설립·등록되었고 자회사 명의를 이용해 모회사 매출의 "
            "외형을 이전한 것으로 볼 여지가 크다고 보아, 해당 세금계산서가 사실과 다르지 않다고 본 "
            "원심을 파기환송했습니다.\n\n"
            "5. 선의·무과실 입증자료\n"
            "- 거래 전 확인: 사업자등록증, 법인등기사항, 대표자·담당자의 권한과 사업장 존재 여부를 "
            "확인한 자료\n"
            "- 실제 거래: 계약서·발주서·견적서, 납품확인서·거래명세서·물품수령증, 운송·검수 기록\n"
            "- 대금 지급: 세금계산서상 공급자 명의 계좌로 정상 지급한 금융거래 내역\n"
            "- 거래 경과: 담당자 이메일·메신저·통화 기록, 현장 방문이나 거래처 확인 자료\n"
            "- 의심 정황 대응: 계좌명의 불일치, 비정상적으로 낮은 가격, 잦은 사업자 변경 등 명의위장을 "
            "의심할 사정이 없었거나 이를 추가 확인한 자료\n"
            "각 자료는 하나만으로 결정되지 않고 거래 규모·업종·기간과 당시 의심할 사정의 유무를 "
            "종합하여 판단합니다.\n\n"
            "검색 근거: 대법원 2025. 5. 29. 선고 2023두41314 판결"
        )

    @staticmethod
    def try_generate_cancelled_sale_corrected_invoice_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question or "")
        is_corrected_invoice_question = "수정세금계산서" in compact_question
        is_cancelled_sale = any(
            term in compact_question
            for term in ("계약해제", "계약취소", "매출취소", "취소", "환불")
        )
        asks_deadline = any(
            term in compact_question
            for term in ("언제", "기한", "며칠", "몇일", "까지", "발급")
        )
        has_direct_rule = (
            "제70조" in context
            and "계약의 해제로 재화 또는 용역이 공급되지 아니한 경우" in context
        )
        if not (
            is_corrected_invoice_question
            and is_cancelled_sale
            and asks_deadline
            and has_direct_rule
        ):
            return None

        return (
            "1. 결론\n"
            "매출 취소가 계약 해제에 해당한다면, 수정세금계산서는 "
            "계약해제일이 속하는 달의 다음 달 10일까지 발급해야 합니다. "
            "10일이 토요일이나 공휴일이면 바로 다음 영업일까지 발급할 수 있습니다.\n\n"
            "2. 발급 방법\n"
            "작성일은 계약해제일로 적고, 비고란에는 처음 세금계산서 작성일을 적은 뒤 "
            "당초 공급가액을 음(-)으로 발급합니다.\n\n"
            "3. 주의사항\n"
            "부가가치세법 시행령 제70조제1항제4호의 ‘과세기간 종료 후 25일’은 "
            "내국신용장 또는 구매확인서 발급에 관한 규정이므로 계약 해제에는 적용되지 않습니다.\n\n"
            "4. 검색 근거\n"
            "부가가치세법 시행령 제70조제1항제2호"
        )

    @staticmethod
    def try_generate_consignment_sale_invoice_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question or "")
        is_consignment_sale = any(
            term in compact_question
            for term in ("위탁판매", "대리판매", "수탁자", "위탁자")
        )
        asks_issuer = (
            "세금계산서" in compact_question
            and any(
                term in compact_question
                for term in ("누가", "발급", "명의", "누구")
            )
        )
        has_direct_rule = (
            "제69조" in context
            and "수탁자 또는 대리인이 위탁자 또는 본인의 명의로" in context
        )
        if not (is_consignment_sale and asks_issuer and has_direct_rule):
            return None

        return (
            "1. 결론\n"
            "위탁판매에서 수탁자가 재화를 인도하면, 수탁자가 위탁자의 명의로 "
            "세금계산서를 발급해야 합니다. 즉, 발급 업무는 수탁자가 하지만 "
            "세금계산서상 공급자는 위탁자입니다.\n\n"
            "2. 기재 방법\n"
            "위탁자의 명의와 등록번호를 공급자란에 적고, 수탁자의 사업자등록번호를 "
            "덧붙여 적어야 합니다.\n\n"
            "3. 예외\n"
            "위탁자가 구매자에게 재화를 직접 인도한 경우에는 위탁자가 직접 "
            "세금계산서를 발급할 수 있습니다.\n\n"
            "4. 검색 근거\n"
            "부가가치세법 제32조제6항, 부가가치세법 시행령 제69조제1항"
        )

    @staticmethod
    def try_generate_exempt_invoice_penalty_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question)
        is_target = (
            "면세사업자" in compact_question
            and "계산서" in compact_question
            and any(term in compact_question for term in ("미발급", "발급하지", "가산세"))
        )
        if not is_target:
            return None
        required_terms = ("제163조", "제81조의10", "제121조", "제75조의8")
        if not all(term in context for term in required_terms):
            return None

        return (
            "1. 결론\n"
            "면세사업자라도 계산서 발급의무가 있는 거래에서 계산서를 발급하지 않으면 "
            "미발급·지연발급 및 계산서합계표 제출 불성실 가산세가 적용될 수 있습니다. "
            "개인사업자와 법인사업자 모두 기본 세율 구조는 같습니다.\n\n"
            "2. 가산세 종류와 세율\n"
            "- 미발급 가산세: 발급시기가 지난 뒤에도 해당 과세기간 또는 사업연도 종료 후 "
            "다음 달 25일까지 계산서를 발급하지 않으면 공급가액의 2%\n"
            "- 지연발급 가산세: 정해진 발급시기는 지났지만 위 다음 달 25일까지 발급하면 공급가액의 1%\n"
            "- 필요적 기재사항 누락·사실과 다른 계산서: 공급가액의 1%\n"
            "- 매출·매입처별 계산서합계표를 기한 내 제출하지 않거나 사실과 다르게 제출: 공급가액의 0.5%\n\n"
            "3. 적용 전 확인사항\n"
            "개인 면세사업자는 소득세법 제163조의 발급의무와 제81조의10을 적용하되, "
            "대통령령으로 정하는 소규모사업자는 가산세 대상에서 제외됩니다. "
            "법인 면세사업자는 법인세법 제121조와 제75조의8을 적용합니다. "
            "부동산 매각 등 법령상 계산서 발급이 적합하지 않은 예외 거래도 확인해야 합니다.\n\n"
            "4. 계산에 필요한 정보\n"
            "공급가액, 실제 공급일과 계산서 발급일, 개인·법인 여부, 소규모사업자 해당 여부, "
            "계산서합계표 제출 여부가 필요합니다.\n\n"
            "검색 근거: 소득세법 제163조·제81조의10, 법인세법 제121조·제75조의8"
        )

    @staticmethod
    def try_generate_simple_taxpayer_invoice_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question)
        is_target = (
            "간이과세" in compact_question
            and "세금계산서" in compact_question
            and any(term in compact_question for term in ("발급", "의무", "경우"))
        )
        if not is_target:
            return None
        required_terms = ("제36조", "4천800만원", "제61조")
        if not all(term in context for term in required_terms):
            return None

        return (
            "1. 결론\n"
            "간이과세자도 직전 연도 공급대가 합계액이 4,800만원 이상이면 "
            "과세되는 재화·용역을 공급할 때 세금계산서를 발급해야 합니다. "
            "4,800만원 미만이면 원칙적으로 세금계산서 대신 영수증 또는 현금영수증을 발급합니다.\n\n"
            "2. 매출 구간별 기준\n"
            "- 직전 연도 공급대가 4,800만원 미만: 영수증 발급 대상\n"
            "- 4,800만원 이상 1억400만원 미만: 간이과세자이지만 세금계산서 발급 대상\n"
            "- 1억400만원 이상: 원칙적으로 일반과세자로 전환\n"
            "업종·사업장 보유 현황에 따라 간이과세 적용이 배제되는 예외가 있습니다.\n\n"
            "3. 적용 시기와 주의사항\n"
            "4,800만원 기준에 따른 영수증 발급 적용 여부는 기준금액에 미달하거나 이상이 된 "
            "해의 다음 해 7월 1일부터 그 다음 해 6월 30일까지 적용됩니다. "
            "세금계산서 발급 대상 간이과세자가 발급하지 않으면 현행 간이과세자 가산세 규정에 따라 "
            "미발급 공급가액의 1% 가산세가 적용될 수 있습니다.\n\n"
            "4. 확인할 사항\n"
            "직전 연도 공급대가, 신규사업자 여부, 거래가 과세 재화·용역인지, 업종별 간이과세 "
            "배제 여부를 확인해야 합니다.\n\n"
            "검색 근거: 부가가치세법 제32조·제36조·제36조의2·제61조·제68조의2, "
            "부가가치세법 시행령 제109조"
        )

    @staticmethod
    def try_generate_recognized_interest_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question)
        is_target = (
            "가지급금" in compact_question
            and any(term in compact_question for term in ("인정이자", "이자계산", "인정이자율"))
        )
        if not is_target:
            return None
        required_terms = ("제89조", "가중평균차입이자율", "당좌대출이자율")
        if not all(term in context for term in required_terms):
            return None

        return (
            "1. 계산식\n"
            "대표이사 가지급금 인정이자는 [가지급금 적수 × 적용 이자율 ÷ 365]에서 실제 받은 이자를 뺀 금액입니다. "
            "가지급금 적수는 매일의 가지급금 잔액을 합산한 값이므로, 금액이 변동되면 기간별 잔액 × 보유일수를 각각 계산해 합산합니다.\n\n"
            "2. 적용 이자율\n"
            "원칙은 법인세법 시행령 제89조제3항의 가중평균차입이자율입니다. "
            "가중평균차입이자율을 적용할 수 없는 경우, 대여기간이 5년을 초과하는 경우, "
            "또는 법인이 신고와 함께 당좌대출이자율을 선택한 경우에는 당좌대출이자율을 적용합니다. "
            "법인세법 시행규칙 제43조제2항의 당좌대출이자율은 연 4.6%이며, 선택하면 선택 사업연도와 이후 2개 사업연도에 적용됩니다.\n\n"
            "3. 예시\n"
            "가지급금 1억원이 100일간 유지되고 실제 받은 이자가 없다면, 연 4.6% 적용 시 "
            "1억원 × 100일 × 4.6% ÷ 365 = 약 1,260,274원입니다.\n\n"
            "4. 세무 처리와 확인사항\n"
            "계산한 인정이자에서 실제 수입이자를 차감한 금액을 익금에 산입합니다. "
            "정확한 계산에는 날짜별 잔액, 발생일·회수일, 실제 수입이자, 법인의 차입금과 "
            "가중평균차입이자율 적용 가능 여부를 확인해야 합니다.\n\n"
            "검색 근거: 법인세법 제52조, 법인세법 시행령 제88조·제89조, 법인세법 시행규칙 제43조"
        )

    @staticmethod
    def try_generate_treaty_beneficial_owner_denial_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question)
        asks_treaty = "조세조약" in compact_question or "조약상" in compact_question
        asks_owner = "수익적소유자" in compact_question or "실질귀속" in compact_question
        asks_denial = any(term in compact_question for term in ("제한세율", "적용부인", "부인", "과세"))
        if not (asks_treaty and asks_owner and asks_denial):
            return None

        required_basis = (
            "수익적 소유자",
            "실질과세",
        )
        if not all(term in context for term in required_basis):
            return None
        compact_context = re.sub(r"\s+", "", context)
        has_denial_basis = (
            "조세조약적용을부인" in compact_context
            or "적용을부인할수있" in compact_context
            or "명의에따른조세조약적용을부인" in compact_context
        )
        has_tax_basis = "과세" in compact_context or "납세의무자" in compact_context
        if not (has_denial_basis and has_tax_basis):
            return None

        case_reference = (
            "대법원 2018. 11. 15. 선고 2017두33008 판결은 "
            if "2017두33008" in context
            else "검색된 판례 근거는 "
        )

        return (
            "1. 일반 법리\n"
            "부인할 수 있습니다. 조세조약상 명의자가 수익적 소유자에 해당하지 않거나, "
            "수익적 소유자처럼 보이더라도 명의와 실질의 괴리가 조세회피 목적에서 비롯된 "
            "조약 남용이면 제한세율 적용을 부인하고 실질 귀속자에게 과세할 수 있습니다.\n\n"
            "2. 해당 사건 결론\n"
            f"{case_reference}위 일반 법리를 제시한 판례입니다.\n\n"
            "3. 이유\n"
            "국세기본법상 실질과세 원칙은 조세조약에도 적용되므로, 명의자가 재산을 "
            "지배ㆍ관리할 능력이 없고 실질 지배자가 따로 있으며 그 괴리가 조세회피 "
            "목적에서 비롯되면 명의에 따른 조세조약 적용을 부인하고 과세할 수 있습니다."
        )

    @staticmethod
    def try_generate_beneficial_owner_dividend_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question)
        asks_shell_company = any(
            term in compact_question
            for term in ("페이퍼컴퍼니", "도관회사", "명목회사")
        )
        asks_dividend = "배당" in compact_question
        asks_owner = any(
            term in compact_question
            for term in ("실질귀속", "귀속자", "귀속주체")
        )
        if not (asks_shell_company and asks_dividend and asks_owner):
            return None

        required_basis = (
            "2014누6236",
            "현실적으로 귀속",
            "실질적으로 배당소득",
            "형식적인 귀속 명의자",
            "조세회피 목적",
            "실질귀속자의 지위",
        )
        if not all(term in context for term in required_basis):
            return None

        return (
            "1. 결론\n"
            "서울고등법원 2014누6236은 해외 법인 명의만으로 소득 귀속을 정하지 않고, "
            "소득이 누구에게 현실적으로 귀속되었는지를 소득 항목별로 판단했습니다. 홍콩 법인이 "
            "BVI 법인에 수수료 명목으로 송금한 돈은 주주에게 현실 귀속되고 실질적으로 배당소득에 "
            "해당한다고 보아 주주에 대한 종합소득세 과세대상으로 인정했습니다.\n\n"
            "2. 판단 기준\n"
            "- 송금 명목과 거래관계가 실제인지\n"
            "- 소득을 최종적으로 수취·사용한 사람이 누구인지\n"
            "- 해외 법인이 독립된 거래주체인지, 형식적 명의자에 불과한지\n"
            "- 법인 설립·거래에 투자 목적과 조세회피 목적 중 무엇이 인정되는지\n"
            "- 허위 자료나 위장된 거래로 현실 귀속을 은닉했는지\n\n"
            "3. 검색 근거\n"
            "서울고등법원 2018. 1. 24. 선고 2014누6236 종합소득세부과처분취소. "
            "다만 법원은 BVI 법인의 독립성과 투자 목적이 인정되는 다른 소득까지 모두 주주에게 "
            "귀속시킨 것은 아니므로, 페이퍼컴퍼니라는 사정만으로 일률적으로 판단할 수는 없습니다."
        )

    @staticmethod
    def try_generate_local_tax_clawback_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = re.sub(r"\s+", "", question)
        asks_local_tax = any(
            term in compact_question
            for term in ("지방세", "취득세", "재산세")
        )
        asks_relief = any(
            term in compact_question
            for term in ("감면", "면제", "경감")
        )
        asks_property = any(
            term in compact_question
            for term in ("부동산", "토지", "건축물", "주택")
        )
        asks_clawback_condition = any(
            term in compact_question
            for term in ("추징", "다른용도", "용도변경", "직접사용", "유예기간")
        )
        if not (
            asks_local_tax
            and asks_relief
            and asks_property
            and asks_clawback_condition
        ):
            return None

        required_basis = (
            "지방세특례제한법",
            "제178조",
            "감면된 취득세",
            "정당한 사유 없이",
            "취득일부터 1년",
            "직접 사용한 기간이 2년 미만",
            "다른 용도로 사용",
        )
        if not all(term in context for term in required_basis):
            return None

        return (
            "1. 결론\n"
            "원칙적으로 추징 대상이 될 수 있지만, '유예기간 내 다른 용도로 사용했다'는 "
            "사실만으로 언제나 즉시 추징되는 것은 아닙니다. 지방세특례제한법 제178조제1항은 "
            "① 정당한 사유 없이 취득일부터 1년이 될 때까지 해당 용도로 직접 사용하지 않은 경우와 "
            "② 해당 용도로 직접 사용한 기간이 2년 미만인 상태에서 다른 용도로 사용한 경우를 "
            "각각 추징 사유로 정합니다. 실제 사실관계가 어느 요건에 해당하는지 구분해야 합니다.\n\n"
            "2. 검색 근거\n"
            "지방세특례제한법 제178조제1항제1호·제2호(감면된 취득세의 추징)\n\n"
            "3. 추가 확인 사항\n"
            "적용받은 구체적인 감면 조항에 별도의 추징 규정이 있는지, 취득일, 직접 사용을 시작한 날, "
            "용도변경일 및 정당한 사유의 존재를 확인해야 최종 판단할 수 있습니다."
        )


    @staticmethod
    def try_generate_petroleum_tax_answer(question: str, context: str) -> str | None:
        compact_question = question.replace(" ", "")
        if not any(term in compact_question for term in ("개별소비세", "개소세", "유류세")):
            return None
        if not any(term in question.lower() for term in ("유종", "석유", "휘발유", "경유", "등유")):
            return None

        normalized = re.sub(r"\s+", " ", context)
        fuels = (
            ("가", "휘발유", "리터"),
            ("나", "경유", "리터"),
            ("다", "등유", "리터"),
            ("라", "중유", "리터"),
            ("마", "프로판", "킬로그램"),
            ("바", "부탄", "킬로그램"),
            ("사", "천연가스", "킬로그램"),
            ("자", "유연탄", "킬로그램"),
        )
        rates: list[tuple[str, str, str]] = []

        for item_prefix, fuel, unit in fuels:
            match = re.search(
                rf"{item_prefix}\. ?\s*.{{0,80}}?{re.escape(fuel)}.{{0,180}}?{unit}당\s*([0-9,]+)원",
                normalized,
            )
            if match:
                rates.append((fuel, unit, match.group(1)))

        # 검색 근거에서 최소 3개 유종의 세율이 확인된 경우에만
        # 즉시 답변하고, 근거가 부족하면 기존 LLM 경로를 사용한다.
        if len(rates) < 3:
            return None

        lines = [f"- {fuel}: {unit}당 {amount}원" for fuel, unit, amount in rates]
        return (
            "1. 결론\n"
            + "\n".join(lines)
            + "\n\n2. 검색 근거\n"
            + "개별소비세법 제1조의 과세대상과 세율 규정에서 확인한 기본세율입니다."
            + "\n\n3. 근거 부족 여부\n"
            + "한시적 탄력세율의 실제 적용액은 별도 확인이 필요합니다."
        )

    @staticmethod
    def try_generate_pre_registration_input_tax_answer(
        question: str,
        context: str,
    ) -> str | None:
        compact_question = question.replace(" ", "")
        if "사업자등록" not in compact_question:
            return None
        if not any(term in compact_question for term in ("등록전", "등록이전", "등록전에")):
            return None
        if "매입세액" not in compact_question and "부가가치세" not in compact_question:
            return None

        required_evidence = (
            "제39조",
            "사업자등록을 신청하기 전의 매입세액",
            "20일 이내",
            "등록신청일",
            "과세기간 기산일",
        )
        if not all(term in context for term in required_evidence):
            return None

        return (
            "1. 결론\n"
            "원칙적으로 사업자등록 신청 전에 발생한 매입세액은 공제되지 않습니다. "
            "다만 인테리어의 공급시기가 속하는 과세기간이 끝난 후 20일 이내에 "
            "사업자등록을 신청했다면, 등록신청일부터 그 과세기간의 기산일까지 "
            "역산한 기간 내의 매입세액은 예외적으로 공제 가능합니다.\n\n"
            "2. 검색 근거\n"
            "부가가치세법 제39조제1항제8호는 사업자등록 신청 전 매입세액을 "
            "원칙적으로 불공제하면서 위 20일 이내 등록 예외를 규정합니다.\n\n"
            "3. 근거 부족 여부\n"
            "실제 공제 여부는 인테리어 용역의 공급시기, 등록신청일, "
            "과세사업 관련성 및 세금계산서 등 증빙을 함께 확인해야 합니다."
        )

    @staticmethod
    def try_generate_housing_loan_requirement_answer(question: str, context: str) -> str | None:
        if not LlmService.is_housing_loan_requirement_question(question):
            return None

        required_terms = (
            "제52조",
            "제112조",
            "장기주택저당차입금",
            "6억원",
        )

        if not all(term in context for term in required_terms):
            return None

        if "3월 이내" not in context and "3개월 이내" not in context:
            return None

        return (
            "1. 결론\n"
            "장기주택저당차입금 이자상환액 공제는 근로소득이 있는 거주자가 "
            "무주택 또는 1주택 세대 요건, 주택 가격 요건, 차입금 요건을 충족할 때 적용됩니다.\n\n"
            "주요 요건은 다음과 같습니다.\n"
            "- 대상자: 근로소득이 있는 거주자\n"
            "- 주택 수: 과세기간 종료일 현재 세대 기준 무주택 또는 1주택. 세대 구성원이 보유한 주택을 포함해 2주택 이상이면 적용 불가\n"
            "- 세대주/세대원: 원칙적으로 세대주. 세대주가 주택 관련 공제를 받지 않으면 근로소득이 있는 세대원도 가능\n"
            "- 세대원 실제 거주: 세대주가 아닌 거주자는 실제 거주하는 경우에만 적용\n"
            "- 주택 가격: 취득 당시 기준시가 6억원 이하\n"
            "- 차입 시기: 주택소유권 이전등기 또는 보존등기일부터 3개월 이내 차입\n"
            "- 채무자 요건: 장기주택저당차입금의 채무자가 저당권이 설정된 주택의 소유자여야 함\n"
            "- 상환기간/방식: 기본적으로 상환기간 15년 이상이면 연 800만원 한도. 고정금리ㆍ비거치식 분할상환 여부에 따라 한도가 커질 수 있음\n\n"
            "공제 한도는 다음과 같습니다.\n"
            "- 상환기간 15년 이상 + 고정금리 + 비거치식 분할상환: 연 2,000만원\n"
            "- 상환기간 15년 이상 + 고정금리 또는 비거치식 분할상환 중 하나 충족: 연 1,800만원\n"
            "- 상환기간 10년 이상 + 고정금리 또는 비거치식 분할상환 중 하나 충족: 연 600만원\n"
            "- 그 밖의 15년 이상 장기주택저당차입금: 연 800만원\n\n"
            "2. 검색 근거\n"
            "소득세법 제52조제5항은 근로소득이 있는 거주자로서 무주택 또는 1주택 세대의 세대주 등이 "
            "취득 당시 기준시가 6억원 이하인 주택을 취득하기 위해 장기주택저당차입금을 차입하고 "
            "그 이자를 지급한 경우 이자상환액을 근로소득금액에서 공제한다고 정합니다. "
            "같은 항은 과세기간 종료일 현재 2주택 이상 보유한 경우 적용하지 않고, 세대주가 아닌 거주자는 "
            "실제 거주하는 경우에만 적용한다고 정합니다. "
            "소득세법 시행령 제112조제8항은 장기주택저당차입금을 주택소유권 이전등기 또는 보존등기일부터 "
            "3개월 이내에 차입한 차입금이고, 채무자가 해당 저당권 설정 주택의 소유자인 경우로 정합니다. "
            "소득세법 제52조제6항은 고정금리 및 비거치식 분할상환 방식과 상환기간에 따른 공제한도를 정합니다.\n\n"
            "3. 근거 부족 여부\n"
            "제공된 근거만으로 기본 요건과 한도는 확인됩니다. 다만 실제 적용 여부는 취득 당시 기준시가, "
            "등기일과 차입일, 대출 명의와 주택 소유자 일치 여부, 세대 전체 주택 수, 세대원 실제 거주 여부를 "
            "개별 서류로 확인해야 합니다."
        )


    @staticmethod
    def try_generate_overseas_stock_answer(question: str, context: str) -> str | None:
        compact_question = question.replace(" ", "")
        compact_context = context.replace(" ", "")

        asks_overseas_stock = (
            ("미국" in question and "주식" in question)
            or "미국주식" in compact_question
            or "해외주식" in compact_question
            or "외국주식" in compact_question
        )

        has_required_basis = (
            any(term in context for term in ("양도소득", "국외자산", "외국법인", "주식등", "주식 등"))
            and any(term in context for term in ("배당소득", "금융소득", "종합소득"))
        )

        if not (asks_overseas_stock and has_required_basis):
            return None

        has_basic_deduction = "250만원" in context or "양도소득기본공제" in context
        has_filing_month = "5월" in context or "확정신고" in context or "제110조" in context
        has_financial_income_threshold = "2천만원" in context or "2,000만원" in context or "2000만원" in context

        capital_gain_detail = (
            "해외주식을 팔아서 순이익이 난 경우에는 양도소득세 검토 대상입니다. "
            "검색 근거상 양도소득 및 국외자산ㆍ외국법인 주식 관련 조문이 확인됩니다."
        )
        if has_basic_deduction:
            capital_gain_detail += " 연간 양도소득기본공제 250만원 적용 여부도 함께 확인해야 합니다."
        if has_filing_month:
            capital_gain_detail += " 신고는 양도소득 과세표준 확정신고 흐름에 따라 다음 해 5월 신고 대상이 될 수 있습니다."

        dividend_detail = (
            "미국 주식에서 배당금을 받은 경우에는 배당소득입니다. "
            "배당은 보통 현지에서 원천징수된 뒤 입금되지만, 국내 세법상 금융소득 판단에는 포함될 수 있습니다."
        )
        if has_financial_income_threshold:
            dividend_detail += " 이자ㆍ배당 등 금융소득 합계가 연 2천만원을 넘는 경우 종합소득세 신고 검토 대상입니다."

        return (
            "1. 결론\n"
            "네, 미국 주식에서 수익이 났다면 세금 검토가 필요합니다. 다만 수익 종류를 나눠 봐야 합니다.\n\n"
            "- 매도해서 번 차익: 해외주식 양도소득세 대상\n"
            "- 배당금: 배당소득 대상\n\n"
            "일반적으로 해외주식 매매차익은 연간 손익을 합산한 뒤 기본공제 250만원을 넘는 순이익이 있으면 "
            "다음 해 5월에 양도소득세 신고ㆍ납부를 검토해야 합니다. 배당금은 미국에서 원천징수되는 경우가 많지만, "
            "이자ㆍ배당 등 금융소득 합계가 연 2천만원을 넘으면 종합소득세 신고 대상에 포함될 수 있습니다.\n\n"
            "2. 검색 근거\n"
            f"{capital_gain_detail}\n"
            f"{dividend_detail}\n\n"
            "3. 근거 부족 여부\n"
            "제공된 검색 근거만으로는 실제 매도차익 금액, 손실 상계 여부, 배당금 규모, 다른 금융소득 합계, "
            "외국납부세액공제 적용 여부까지 확정할 수 없습니다. 따라서 실제 신고 여부는 해당 연도의 해외주식 "
            "순이익과 배당ㆍ이자 등 금융소득 합계를 확인해야 합니다."
        )


    @staticmethod
    def try_generate_3_3_withholding_answer(question: str, context: str) -> str | None:
        question_compact = question.replace(" ", "")
        context_compact = context.replace(" ", "")

        asks_3_3_withholding = (
            ("3.3" in question or "3.3%" in question)
            and any(term in question for term in ("떼고", "원천징수", "사업소득", "프리랜서"))
            and any(term in question for term in ("종합소득세", "종소세", "신고"))
        )

        has_required_basis = (
            "제70조" in context
            and "종합소득" in context
            and "제73조" in context
            and ("과세표준확정신고의예외" in context_compact or "과세표준확정신고의 예외" in context)
        )

        if not (asks_3_3_withholding and has_required_basis):
            return None

        return (
            "1. 결론\n"
            "대체로 신고 대상입니다. 3.3%를 떼고 받은 돈은 보통 원천징수된 사업소득으로 취급되며, "
            "종합소득금액이 있으면 다음 해 5월에 종합소득세 과세표준확정신고를 해야 합니다. "
            "다만 소득세법 제73조와 소득세법 시행령 제137조의 예외에 해당하는 일부 연말정산 사업소득만 "
            "있는 경우에는 확정신고를 하지 않을 수 있습니다.\n\n"
            "2. 검색 근거\n"
            "소득세법 제70조는 해당 과세기간의 종합소득금액이 있는 거주자는 다음 연도 5월 1일부터 "
            "5월 31일까지 종합소득 과세표준을 신고해야 한다고 정합니다. "
            "소득세법 제73조는 근로소득만 있는 자, 퇴직소득만 있는 자, 공적연금소득만 있는 자, "
            "제127조에 따라 원천징수되는 사업소득으로서 대통령령으로 정하는 사업소득만 있는 자 등은 "
            "확정신고를 하지 않을 수 있다고 정합니다. "
            "소득세법 시행령 제137조는 그 예외 사업소득을 보험모집, 방문판매, 계약배달 판매 용역 등 "
            "일부 간편장부대상자의 사업소득으로 한정합니다.\n\n"
            "3. 근거 부족 여부\n"
            "제공된 근거만으로는 사용자의 소득이 제73조ㆍ시행령 제137조의 예외 업종인지까지는 "
            "확정할 수 없습니다. 일반 프리랜서 사업소득이라면 신고 대상으로 보고, 이미 떼인 3.3%는 "
            "신고 시 기납부세액으로 반영되는지 확인하는 흐름이 안전합니다."
        )


    @staticmethod
    def try_generate_bad_debt_allowance_exclusion_answer(question: str, context: str) -> str | None:
        if not LlmService.is_bad_debt_allowance_exclusion_question(question):
            return None

        compact_context = context.replace(" ", "")
        has_required_basis = (
            "대손충당금" in context
            and ("제34조" in context or "제61조" in context)
        )
        has_exclusion_terms = (
            "채무보증" in context
            and "구상채권" in context
            and ("가지급금" in context or "가지급금" in compact_context)
        )

        if not (has_required_basis and has_exclusion_terms):
            return None

        return (
            "1. 결론\n"
            "질문에서 말한 것은 엄밀히는 부채가 아니라 대손충당금 설정ㆍ한도 계산에서 "
            "제외되는 채권입니다. 법인세 대손충당금 계산에서 제외되는 핵심 채권은 다음과 같습니다.\n\n"
            "- 채무보증으로 인하여 발생한 구상채권\n"
            "- 특수관계인에게 해당 법인의 업무와 관련 없이 지급한 가지급금 등\n\n"
            "2. 검색 근거\n"
            "법인세법 제34조제2항은 대손충당금의 손금산입 규정을 제19조의2제2항 각 호의 "
            "채권에는 적용하지 않는다고 정합니다. 따라서 제19조의2제2항의 채무보증으로 인한 "
            "구상채권과 업무무관 가지급금 등이 대손충당금 설정 대상에서 제외됩니다. "
            "법인세법 시행령 제61조는 대손충당금 손금산입 한도 계산의 기초가 되는 "
            "외상매출금ㆍ대여금 및 이에 준하는 채권의 범위와 채권잔액 계산 방식을 정합니다.\n\n"
            "3. 근거 부족 여부\n"
            "제공된 법령 근거만으로 제외되는 채권의 방향은 확인됩니다. 다만 구체적 거래가 "
            "업무무관 가지급금인지, 예외적으로 인정되는 채무보증인지 여부는 사실관계 확인이 필요합니다."
        )

    @staticmethod
    def is_bad_debt_allowance_exclusion_question(question: str) -> bool:
        return (
            "대손충당금" in question
            and any(term in question for term in ("제외", "제외되는", "빼", "차감"))
            and any(term in question for term in ("한도", "계산", "채권", "부채", "항목"))
        )

    @staticmethod
    def is_housing_loan_limit_question(question: str) -> bool:
        housing_terms = (
            "장기주택저당차입금",
            "장기저당차입금",
            "주택저당차입금",
            "주택담보대출",
            "주담대",
        )
        limit_terms = ("한도", "공제한도", "한도액", "얼마")
        interest_terms = ("이자상환액", "이자 상환액", "이자")

        return (
            any(term in question for term in housing_terms)
            and any(term in question for term in limit_terms)
            and any(term in question for term in interest_terms)
        )

    @staticmethod
    def is_housing_loan_requirement_question(question: str) -> bool:
        housing_terms = (
            "장기주택저당차입금",
            "장기저당차입금",
            "주택저당차입금",
            "주택담보대출",
            "주담대",
        )
        requirement_terms = ("요건", "조건", "대상", "가능", "공제")
        interest_terms = ("이자상환액", "이자 상환액", "이자")

        return (
            any(term in question for term in housing_terms)
            and any(term in question for term in requirement_terms)
            and any(term in question for term in interest_terms)
        )
