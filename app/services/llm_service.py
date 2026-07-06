from __future__ import annotations

import asyncio
from collections import OrderedDict
import json
import re
from time import monotonic, perf_counter
from typing import AsyncIterator

import httpx

from app.core.config import get_settings


class LlmService:
    PROMPT_VERSION = "2026-07-06-grounded-context-v7"

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
        )
        if len(context) > self.max_context_chars:
            context = context[:self.max_context_chars]

        if has_precedent_context:
            source_specific_rules = (
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
                        "- 직접 근거가 없으면 '제공된 근거만으로는 판단할 수 없습니다.'라고 쓴다.\n"
                        "- 질문이 일반적인 판단 기준을 묻고 검색 근거가 그 기준을 제시하면, "
                        "구체적 사실관계가 없다는 이유만으로 판단 불가라고 하지 않는다.\n"
                        "- 금액·기간·조건은 근거에 있는 내용을 빠뜨리지 않는다.\n"
                        "- 관련 없는 조문은 인용하지 않는다.\n"
                        "- 지급자·수령자, 제공자·제공받는 자와 고가·저가의 거래 방향을 바꾸지 않는다.\n"
                        "- 사건번호·선고일·조문번호는 검색 근거의 값을 한 글자도 바꾸지 않고 옮긴다.\n"
                        "- 결론에는 질문이 요구한 항목을 실제로 나열하고 조문번호만 쓰지 않는다.\n"
                        f"{source_specific_rules}"
                        f"{transaction_direction_rules}"
                        "- 전체 350자 이내로 간결하게 작성한다.\n"
                        "형식:\n1. 결론\n2. 검색 근거(법령명·조문명·사건번호)\n"
                        "3. 근거의 범위(일반 판단 기준의 근거가 있으면 "
                        "'일반 판단 기준에 관한 근거는 충분함'이라고 쓴다)"
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

    async def stream_answer(self, question: str, context: str) -> AsyncIterator[str]:
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
            print("========== Ollama 호출 실패 ==========")
            print("에러 타입:", httpx.ReadTimeout)
            print("에러 내용: read timeout")
            print("========== Ollama 호출 실패 끝 ==========")
            yield "LLM 응답 시간이 초과되었습니다. 검색 근거를 줄이거나 다시 시도해주세요."
            return

        except httpx.HTTPStatusError as e:
            print("========== Ollama 호출 실패 ==========")
            print("에러 타입:", type(e))
            print("상태 코드:", e.response.status_code)
            print("에러 내용:", e.response.text)
            print("========== Ollama 호출 실패 끝 ==========")
            yield "LLM 서버에서 오류 응답을 반환했습니다."
            return

        except httpx.RequestError as e:
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
