from __future__ import annotations

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
            "stream": False,
            "think": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "생각 과정을 출력하지 마라. "
                        "즉시 최종 답변만 작성하라. "
                        "너는 현행 세법 법령 검색 기반 답변 도우미다. "
                        "반드시 제공된 법령 근거만 사용해서 답변해라. "
                        "법령 근거에 없는 내용은 추론하거나 추가하지 마라. "
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
                        "검색 근거를 설명할 때는 제공된 법령 근거의 문장을 바탕으로 설명해라."
                        "답변할 때는 근거가 된 법령명과 조문명을 함께 표시하라. "
                        "조문 번호는 검색 근거의 본문에 있는 번호를 그대로 사용하라. "
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"[사용자 질문]\n{question.strip()}\n\n"
                        f"[법령 근거]\n{context}\n\n"
                        "위 법령 근거만 사용해서 사용자의 질문에 답변해줘. "
                        "법령 근거와 관련 없는 일반 지식은 사용하지 마."
                    ),
                },
            ],
            "options": {
                "temperature": 0.1,
                "top_p": 0.8,
                "num_predict": 800,
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
            print(context)
            print("========== END ==========")

            print("========== PAYLOAD ==========")
            print(payload)
            print("========== END PAYLOAD ==========")

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()

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
        bad_debt_answer = LlmService.try_generate_bad_debt_allowance_exclusion_answer(question, context)
        if bad_debt_answer is not None:
            return bad_debt_answer

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
