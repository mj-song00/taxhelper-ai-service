from __future__ import annotations

import httpx

from app.core.config import get_settings


class LlmService:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout_sec = settings.request_timeout_sec

    async def generate_answer(self, question: str, context: str) -> str:
        url = f"{self.base_url}/api/chat"

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "너는 세법/판례 검색 기반 답변 도우미다. "
                        "반드시 제공된 검색 근거만 사용해서 답변해라. "
                        "근거가 부족하면 부족하다고 말해라."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"[사용자 질문]\n{question}\n\n"
                        f"[검색 근거]\n{context}"
                    ),
                },
            ],
        }

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        message = data.get("message", {})
        content = message.get("content")

        if not isinstance(content, str):
            return "LLM 응답을 읽을 수 없습니다."

        return content