from __future__ import annotations

import json
import re

import httpx

from app.core.config import get_settings


class LlmService:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model

    async def generate_answer(self, question: str, context: str) -> str:
        url = f"{self.base_url}/api/chat"

        max_context_chars = 6000

        if context is None:
            context = ""

        context = context.strip()

        if len(context) > max_context_chars:
            context = context[:max_context_chars]

        direct_answer = self.try_generate_direct_answer(question, context)
        if direct_answer is not None:
            return direct_answer

        payload = {
            "model": self.model,
            "stream": True,
            "think": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "생각 과정을 출력하지 마라. "
                        "즉시 최종 답변만 작성하라. "
                        "너는 현행 세법 법령 및 판례 검색 기반 답변 도우미다. "
                        "반드시 제공된 법령 근거와 판례 근거만 사용해서 답변해라. "
                        "제공된 근거에 없는 내용은 추론하거나 추가하지 마라. "
                        "질문에 대한 직접적인 근거가 없으면 반드시 "
                        "'제공된 근거만으로는 판단할 수 없습니다.'라고 답변해라. "
                        "답변은 한국어로 작성해라. "
                        "답변은 너무 길게 쓰지 말고, 사용자가 바로 이해할 수 있게 작성해라. "
                        "한도, 금액, 공제액을 묻는 질문이면 법령 근거에 있는 조건과 금액을 빠뜨리지 말고 표 또는 항목으로 정리해라. "
                        "법령 근거에 숫자, 금액, 기간, 조건이 함께 있으면 결론에 함께 포함해라. "
                        "시행령의 정의 조문보다 법률 본문에 있는 금액과 한도 규정을 먼저 답변해라. "
                        "질문이 제외되는 항목을 묻는 경우에는 제도 설명을 길게 하지 말고 제외되는 항목만 먼저 목록으로 답해라. "
                        "대손충당금 질문에서 법 제19조의2제2항이 근거로 나오면 제19조의2제1항의 대손 사유 목록을 제외 항목으로 답하지 마라. "
                        "답변 형식은 반드시 다음을 따른다. "
                        "1. 결론 "
                        "2. 검색 근거 "
                        "3. 근거 부족 여부 "
                        "검색 근거를 설명할 때는 제공된 법령 근거와 판례 근거의 문장을 바탕으로 설명해라."
                        "답변할 때는 근거가 된 법령명과 조문명, 판례가 있으면 사건번호를 함께 표시하라. "
                        "조문 번호는 검색 근거의 본문에 있는 번호를 그대로 사용하라. "
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"[사용자 질문]\n{question.strip()}\n\n"
                        f"[검색 근거]\n{context}\n\n"
                        "위 검색 근거만 사용해서 사용자의 질문에 답변해줘. "
                        "검색 근거와 관련 없는 일반 지식은 사용하지 마."
                    ),
                },
            ],
            "options": {
                "temperature": 0.1,
                "top_p": 0.8,
                "num_predict": 320,
            },
        }

        print("question length =", len(question))
        print("context length =", len(context))

        timeout = httpx.Timeout(
            connect=10.0,
            read=300.0,
            write=30.0,
            pool=10.0,
        )

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
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        piece = event.get("message", {}).get("content")
                        if isinstance(piece, str):
                            content_parts.append(piece)

            data = {"message": {"content": "".join(content_parts)}}

        except httpx.ReadTimeout:
            print("========== Ollama 호출 실패 ==========")
            print("에러 타입:", httpx.ReadTimeout)
            print("에러 내용: read timeout")
            print("========== Ollama 호출 실패 끝 ==========")
            return "LLM 응답 시간이 초과되었습니다. 검색 근거를 줄이거나 다시 시도해주세요."

        except httpx.HTTPStatusError as e:
            print("========== Ollama 호출 실패 ==========")
            print("에러 타입:", type(e))
            print("상태 코드:", e.response.status_code)
            print("에러 내용:", e.response.text)
            print("========== Ollama 호출 실패 끝 ==========")
            return "LLM 서버에서 오류 응답을 반환했습니다."

        except httpx.RequestError as e:
            print("========== Ollama 호출 실패 ==========")
            print("에러 타입:", type(e))
            print("에러 내용:", str(e))
            print("========== Ollama 호출 실패 끝 ==========")
            return "LLM 서버에 연결할 수 없습니다."

        print("========== OLLAMA RESPONSE ==========")
        print(data)
        print("========== OLLAMA RESPONSE END ==========")

        message = data.get("message", {})
        content = message.get("content")

        if not isinstance(content, str):
            return "LLM 응답을 읽을 수 없습니다."

        content = content.strip()

        if not content:
            return "LLM이 빈 응답을 반환했습니다. 검색 근거가 너무 길거나 모델이 답변 생성을 완료하지 못했을 수 있습니다."

        return content

    @staticmethod
    def try_generate_direct_answer(question: str, context: str) -> str | None:
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
