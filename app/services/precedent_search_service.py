from __future__ import annotations

import re

from app.schemas.retrieval import Pagination, PrecedentChunk
from app.services.chunk_client import ChunkSearchClient
from app.services.chunk_search_service import ChunkSearchService


class PrecedentSearchService:
    PRECEDENT_REQUEST_TERMS = (
        "판례", "판결", "판시사항", "판결요지", "사건번호",
        "대법원", "고등법원", "지방법원", "행정법원", "조세심판원",
    )
    CHUNK_TYPE_HINTS = {
        "ISSUE": ("판시사항", "쟁점"),
        "SUMMARY": ("판결요지", "요지"),
        "FULL_TEXT": ("판례내용", "전문", "본문"),
    }

    COURT_PATTERN = re.compile(
        r"(?:대법원|헌법재판소|조세심판원|고등법원|지방법원|행정법원|특허법원|회생법원|가정법원)"
    )

    def __init__(self, client: ChunkSearchClient, candidate_size: int) -> None:
        self.client = client
        self.candidate_size = candidate_size

    async def retrieve_chunks(
        self,
        conditions: dict,
        top_k: int,
    ) -> tuple[list[PrecedentChunk], Pagination]:
        raw_chunks, page_info = await self.client.fetch_chunks(
            candidate_page=conditions.get("page", 1),
            candidate_size=conditions.get("size", self.candidate_size),
            chunks_path=self.client.precedent_chunks_path,
            keywords=conditions["keywords"],
            rewritten_query=conditions["rewritten_query"],
            court_names=conditions["court_names"],
            case_numbers=conditions["case_numbers"],
        )

        chunks = [self._to_chunk(item) for item in raw_chunks]

        ranked = self.rank_chunks(
            chunks=chunks,
            keywords=conditions["keywords"],
            chunk_types=conditions["chunk_types"],
            query_text=conditions["original_question"],
            top_k=len(chunks),
        )

        return ranked[:top_k], Pagination(**page_info)

    def build_search_conditions(self, question: str) -> dict:
        normalized = " ".join(question.split())
        keywords = ChunkSearchService.extract_keywords(normalized)
        tax_domain = ChunkSearchService.infer_tax_domain(keywords)
        court_names = self.extract_court_names(normalized)
        case_numbers = self.extract_case_numbers(normalized)
        chunk_types = self.extract_chunk_types(normalized)
        intent = self.detect_intent(normalized, chunk_types)

        rewritten_query = self.build_rewritten_query(
            keywords=keywords,
            court_names=court_names,
            case_numbers=case_numbers,
            chunk_types=chunk_types,
        )

        return {
            "intent": intent,
            "tax_domain": tax_domain,
            "original_question": question,
            "keywords": keywords,
            "court_names": court_names,
            "case_numbers": case_numbers,
            "chunk_types": chunk_types,
            "rewritten_query": rewritten_query,
            "page": 1,
            "size": self.candidate_size,
        }

    @classmethod
    def should_search(cls, question: str) -> bool:
        """판례를 요청한 질문에서만 판례 검색을 실행한다.

        세율·금액·신고 기한처럼 법령만으로 답할 수 있는 질문에
        판례 전문을 섞지 않는다.
        """
        compact = re.sub(r"\s+", "", question)
        if cls.extract_case_numbers(question):
            return True
        return any(term in compact for term in cls.PRECEDENT_REQUEST_TERMS)

    @staticmethod
    def build_search_prompt(question: str, conditions: dict) -> str:
        chunk_type_labels = {
            "ISSUE": "판시사항",
            "SUMMARY": "판결요지",
            "FULL_TEXT": "판례내용",
        }

        preferred_types = ", ".join(
            chunk_type_labels.get(chunk_type, chunk_type)
            for chunk_type in conditions["chunk_types"]
        ) or "없음"

        return (
            "사용자 질문을 판례 검색용 조건으로 변환한다.\n"
            f"- 원문 질문: {question}\n"
            f"- 검색 의도: {conditions['intent']}\n"
            f"- 세목: {conditions['tax_domain']}\n"
            f"- 법원명: {', '.join(conditions['court_names']) or '없음'}\n"
            f"- 사건번호: {', '.join(conditions['case_numbers']) or '없음'}\n"
            f"- 선호 청크 유형: {preferred_types}\n"
            f"- 핵심 키워드: {', '.join(conditions['keywords']) or '없음'}\n"
            f"- 검색 질의문: {conditions['rewritten_query']}"
        )

    def build_prompt_context(
        self,
        chunks: list[PrecedentChunk],
        max_content_chars: int = 800,
    ) -> str:
        if not chunks:
            return ""

        lines: list[str] = []
        preferred_order = {
            "ISSUE": 0,
            "SUMMARY": 1,
            "REFERENCE_ARTICLE": 2,
            "REFERENCE_CASE": 3,
            "FULL_TEXT": 4,
        }
        context_chunks = sorted(
            chunks,
            key=lambda chunk: preferred_order.get((chunk.chunk_type or "").upper(), 99),
        )

        for idx, chunk in enumerate(context_chunks, start=1):
            source = " / ".join(
                value
                for value in [
                    chunk.title,
                    chunk.metadata.get("caseName"),
                    chunk.metadata.get("courtName"),
                    chunk.metadata.get("caseNumber"),
                    chunk.chunk_type,
                ]
                if value
            )
            header = f"[{idx}] {source}" if source else f"[{idx}]"

            lines.append(header)
            if chunk.precedent_id:
                lines.append(f"precedentId: {chunk.precedent_id}")
            lines.append(
                ChunkSearchService.truncate_text(
                    chunk.content.strip(),
                    max_content_chars,
                )
            )
            lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def extract_court_names(question: str) -> list[str]:
        matches = PrecedentSearchService.COURT_PATTERN.findall(question)
        return ChunkSearchService.deduplicate_keep_order(matches)

    @staticmethod
    def extract_case_numbers(question: str) -> list[str]:
        patterns = re.findall(r"\d{4}[가-힣]+\d+", question)
        patterns.extend(re.findall(r"조심\d+[가-힣]*\d*", question))
        cleaned = [re.sub(r"\s+", "", item) for item in patterns]
        return ChunkSearchService.deduplicate_keep_order(cleaned)

    @staticmethod
    def extract_chunk_types(question: str) -> list[str]:
        lowered = question.lower()
        chunk_types: list[str] = []

        for chunk_type, hints in PrecedentSearchService.CHUNK_TYPE_HINTS.items():
            if any(hint in lowered for hint in hints):
                chunk_types.append(chunk_type)

        return chunk_types

    @staticmethod
    def detect_intent(question: str, chunk_types: list[str]) -> str:
        lowered = question.lower()

        if "ISSUE" in chunk_types or "판시사항" in lowered:
            return "판시사항 확인"

        if "SUMMARY" in chunk_types or "판결요지" in lowered:
            return "판결요지 확인"

        if any(key in lowered for key in ("유사", "관련 판례", "판례")):
            return "유사 판례 검색"

        if any(key in lowered for key in ("요건", "성립", "충족")):
            return "쟁점 요건 확인"

        return "판례 해석 및 적용 기준 확인"

    @staticmethod
    def build_rewritten_query(
        keywords: list[str],
        court_names: list[str],
        case_numbers: list[str],
        chunk_types: list[str],
    ) -> str:
        query_terms: list[str] = []
        query_terms.extend(court_names)
        query_terms.extend(case_numbers)
        query_terms.extend(keywords)
        query_terms.extend(chunk_types)

        return " ".join(ChunkSearchService.deduplicate_keep_order(query_terms))

    @staticmethod
    def rank_chunks(
        chunks: list[PrecedentChunk],
        keywords: list[str],
        chunk_types: list[str],
        query_text: str,
        top_k: int,
    ) -> list[PrecedentChunk]:
        del keywords

        raw_tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", query_text)
        query_terms: list[str] = []
        seen: set[str] = set()

        for token in raw_tokens:
            normalized = ChunkSearchService.normalize_search_token(token)

            if len(normalized) < 2 or ChunkSearchService.is_unwanted_verb(normalized):
                continue

            lowered = normalized.lower()

            if lowered in seen:
                continue

            seen.add(lowered)
            query_terms.append(lowered)

        if not query_terms:
            for chunk in chunks:
                chunk.score = 0.0
            return chunks[:top_k]

        preferred_types = {chunk_type.upper() for chunk_type in chunk_types}
        scored: list[tuple[float, PrecedentChunk]] = []

        for chunk in chunks:
            title_text = (chunk.title or "").lower()
            content_text = (chunk.content or "").lower()
            chunk_type_text = (chunk.chunk_type or "").lower()

            score = 0.0

            for term in query_terms:
                title_hits = title_text.count(term)
                content_hits = content_text.count(term)
                # 긴 FULL_TEXT가 단순 반복 횟수로 상위를 독점하지
                # 못하도록 용어별 횟수를 제한한다.
                score += (min(title_hits, 2) * 3.0) + min(content_hits, 3)

            matched_terms = sum(
                1
                for term in query_terms
                if term in title_text or term in content_text
            )

            overlap_ratio = matched_terms / len(query_terms)
            score += overlap_ratio * 10.0

            if (chunk.chunk_type or "").upper() == "FULL_TEXT":
                score -= 4.0

            # 질의 핵심 용어의 40%도 맞지 않으면 관련 판례로 보지 않는다.
            if overlap_ratio < 0.40:
                chunk.score = 0.0
                scored.append((0.0, chunk))
                continue

            if (
                preferred_types
                and chunk.chunk_type
                and chunk.chunk_type.upper() in preferred_types
            ):
                score += 5.0

            if chunk_type_text and any(term in chunk_type_text for term in query_terms):
                score += 2.0

            chunk.score = round(score, 4)
            scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)

        ranked = [chunk for score, chunk in scored if score > 0]

        return ranked[:top_k]

    @staticmethod
    def _to_chunk(item: dict) -> PrecedentChunk:
        chunk_id = item.get("id") or item.get("chunkId") or item.get("chunk_id") or ""

        content = (
            item.get("content")
            or item.get("chunk")
            or item.get("text")
            or item.get("body")
            or ""
        )

        chunk_type = item.get("chunkType") or item.get("chunk_type")
        precedent_id = item.get("precedentId") or item.get("precedent_id")

        return PrecedentChunk(
            chunk_id=str(chunk_id),
            precedent_id=str(precedent_id) if precedent_id is not None else None,
            chunk_type=str(chunk_type) if chunk_type is not None else None,
            title=item.get("title"),
            content=str(content),
            score=item.get("score"),
            metadata={
                key: value
                for key, value in {
                    **(item.get("metadata") or {}),
                    "caseNumber": item.get("caseNumber") or item.get("case_number"),
                    "caseName": item.get("caseName") or item.get("case_name"),
                    "courtName": item.get("courtName") or item.get("court_name"),
                    "sentencingDate": item.get("sentencingDate") or item.get("sentencing_date"),
                }.items()
                if value is not None
            },
        )
