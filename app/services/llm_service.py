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
                        "너는 세법/판례 검색 기반 답변 도우미다. "
                        "반드시 제공된 검색 근거만 사용해서 답변해라. "
                        "검색 근거에 없는 내용은 추론하거나 추가하지 마라. "
                        "질문에 대한 직접적인 근거가 없으면 반드시 "
                        "'제공된 근거만으로는 판단할 수 없습니다.'라고 답변해라. "
                        "답변은 한국어로 작성해라. "
                        "답변은 너무 길게 쓰지 말고, 사용자가 바로 이해할 수 있게 작성해라. "
                        "답변 형식은 반드시 다음을 따른다. "
                        "1. 결론 "
                        "2. 검색 근거 "
                        "3. 근거 부족 여부 "
                        "검색 근거를 설명할 때는 사용자가 제공받은 근거의 문장을 바탕으로 설명해라."
                        "답변할 때는 근거가 된 법령명과 조문명을 함께 표시하라. "
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