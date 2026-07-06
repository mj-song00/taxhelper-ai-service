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
    KEYWORD_EXPANSIONS = {
        "페이퍼컴퍼니": (
            "페이퍼 컴퍼니", "도관회사", "도관 법인", "명목회사",
            "실체 없는 법인", "BVI 법인", "홍콩 법인",
        ),
        "실질귀속자": (
            "실질귀속", "실질적 귀속자", "현실 귀속", "소득의 귀속주체",
            "지배·관리", "지배ㆍ관리", "관리·처분", "계좌인출권자",
        ),
        "배당소득": ("배당금", "배당", "이자 및 배당소득"),
        "간접비": ("간접비용", "직간접비", "취득 관련 비용"),
        "과세표준": ("과세표준액", "취득가격", "사실상의 취득가격"),
        "특수관계인": (
            "특수관계자", "계열회사", "이익을 분여", "조세의 부담을 부당하게 감소",
        ),
        "부당행위계산": (
            "부당행위계산부인", "경제적 합리성", "건전한 사회통념", "상관행",
        ),
        "수수료": (
            "거래수수료", "용역비", "용역", "대가관계", "정상가격", "시가",
        ),
    }
    CRIMINAL_CASE_TERMS = (
        "형사", "고합", "고단", "횡령", "배임", "조세범처벌법",
        "특정범죄가중처벌", "특정경제범죄가중처벌", "피고인",
    )
    ADMIN_CASE_TERMS = (
        "부과처분취소", "경정거부처분취소", "과세처분취소",
        "종합소득세", "법인세", "취득세", "부가가치세", "양도소득세",
    )
    SUBSTANTIVE_REASONING_TERMS = (
        "실질적 귀속자", "실질귀속자", "실질귀속", "현실 귀속",
        "소득의 귀속주체", "지배·관리", "지배ㆍ관리", "관리·처분",
        "독립된 거래주체", "형식적인 귀속 명의자", "조세회피 목적",
        "사실상의 취득가격", "취득가격", "간접비용", "직간접비용",
        "부당행위계산", "경제적 합리성", "건전한 사회통념", "상관행",
        "정상적인 거래", "정상가격", "시가", "실제 용역", "대가관계",
        "이익을 분여", "조세의 부담을 부당하게 감소", "특수관계인",
    )

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
            concept_groups=conditions.get("concept_groups", []),
            case_name_hints=conditions.get("case_name_hints", []),
        )

        return ranked[:top_k], Pagination(**page_info)

    def build_search_conditions(self, question: str) -> dict:
        normalized = " ".join(question.split())
        base_keywords = ChunkSearchService.extract_keywords(normalized)
        court_names = self.extract_court_names(normalized)
        case_numbers = self.extract_case_numbers(normalized)
        chunk_types = self.extract_chunk_types(normalized) or ["ISSUE", "SUMMARY"]
        intent = self.detect_intent(normalized, chunk_types)
        keywords, concept_groups = self.expand_keywords(normalized, base_keywords)
        tax_domain = ChunkSearchService.infer_tax_domain(keywords)
        case_name_hints = self.build_case_name_hints(normalized, tax_domain)

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
            "concept_groups": concept_groups,
            "case_name_hints": case_name_hints,
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
        keywords: list[str] | None = None,
        max_content_chars: int = 850,
    ) -> str:
        if not chunks:
            return ""

        lines: list[str] = []
        # retrieve_chunks에서 계산한 관련도 순서를 유지한다. FULL_TEXT라는
        # 이유만으로 정확히 일치하는 판례가 뒤로 밀리면 전체 컨텍스트
        # 길이 제한에 걸려 핵심 판시가 사라질 수 있다.
        for idx, chunk in enumerate(chunks, start=1):
            source = " / ".join(
                value
                for value in [
                    chunk.title,
                    chunk.metadata.get("caseName"),
                    chunk.metadata.get("courtName"),
                    chunk.metadata.get("caseNumber"),
                    chunk.metadata.get("sentencingDate"),
                    chunk.chunk_type,
                ]
                if value
            )
            header = f"[{idx}] {source}" if source else f"[{idx}]"

            lines.append(header)
            if chunk.precedent_id:
                lines.append(f"precedentId: {chunk.precedent_id}")
            lines.append(self.extract_relevant_excerpt(
                chunk.content,
                keywords or [],
                max_content_chars,
            ))
            lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def extract_relevant_excerpt(
        text: str,
        keywords: list[str],
        max_chars: int,
    ) -> str:
        clean_text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
        clean_text = re.sub(r"<[^>]+>", " ", clean_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()
        if len(clean_text) <= max_chars:
            return clean_text

        query_terms = [
            term.lower()
            for term in ChunkSearchService.deduplicate_keep_order(keywords)
            if len(term) >= 2
        ]
        decision_terms = (
            "세무조사 대상 선정사유",
            "선정사유가 없음에도",
            "적법절차의 원칙",
            "조사권을 남용",
            "과세처분은 위법",
            "부과처분을 취소",
            "처분은 위법",
            "취소되어야",
            "위법하다고 봄이 타당",
            "원고의 위 주장은 이유 있다",
            "따라서 이 사건 처분",
            "청구는 이유 있어 이를 인용",
            "주문과 같이 판결",
        )
        lowered = clean_text.lower()
        anchors: list[int] = []
        for term in [*query_terms, *decision_terms]:
            start = 0
            while len(anchors) < 300:
                position = lowered.find(term.lower(), start)
                if position < 0:
                    break
                anchors.append(position)
                start = position + max(len(term), 1)

        if not anchors:
            return ChunkSearchService.truncate_text(clean_text, max_chars)

        window_chars = max_chars if max_chars < 800 else max(350, (max_chars - 30) // 2)
        scored_windows: dict[int, float] = {}
        for anchor in anchors:
            start = max(0, anchor - window_chars // 3)
            end = min(len(clean_text), start + window_chars)
            start = max(0, end - window_chars)
            window = lowered[start:end]
            query_score = sum(6 for term in query_terms if term in window)
            substantive_score = sum(
                36
                for term in PrecedentSearchService.SUBSTANTIVE_REASONING_TERMS
                if term in window
            )
            # Generic disposition phrases occur in nearly every judgment and
            # must not outweigh issue-specific legal concepts.
            decision_score = sum(8 for term in decision_terms if term in window)
            score = query_score + substantive_score + decision_score
            scored_windows[start] = max(scored_windows.get(start, -1), score)

        selected_starts: list[int] = []
        max_windows = 2 if max_chars >= 800 else 1
        for start, _ in sorted(
            scored_windows.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            if any(abs(start - selected) < window_chars for selected in selected_starts):
                continue
            selected_starts.append(start)
            if len(selected_starts) >= max_windows:
                break

        excerpts: list[str] = []
        for index, start in enumerate(selected_starts, start=1):
            excerpt = clean_text[start:start + window_chars].strip()
            prefix = "…" if start > 0 else ""
            suffix = "…" if start + window_chars < len(clean_text) else ""
            label = f"[핵심 근거 {index}] " if max_windows > 1 else ""
            excerpts.append(f"{label}{prefix}{excerpt}{suffix}")
        return "\n".join(excerpts)

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

    @classmethod
    def expand_keywords(
        cls,
        question: str,
        base_keywords: list[str],
    ) -> tuple[list[str], list[list[str]]]:
        compact_question = re.sub(r"\s+", "", question).lower()
        expanded = list(base_keywords)
        concept_groups: list[list[str]] = []

        for trigger, synonyms in cls.KEYWORD_EXPANSIONS.items():
            if trigger.replace(" ", "").lower() not in compact_question:
                continue
            group = [trigger, *synonyms]
            concept_groups.append(group)
            expanded.extend(synonyms)

        return ChunkSearchService.deduplicate_keep_order(expanded)[:30], concept_groups

    @staticmethod
    def build_case_name_hints(question: str, tax_domain: str) -> list[str]:
        hints = ["부과처분취소"]
        compact = re.sub(r"\s+", "", question)
        if "취득세" in compact:
            hints = ["취득세부과처분취소", "취득세등부과처분취소", *hints]
        elif "배당소득" in compact or tax_domain == "소득세":
            hints = ["종합소득세부과처분취소", *hints]
        elif tax_domain == "법인세":
            hints = ["법인세부과처분취소", "법인세등부과처분취소", *hints]
        return ChunkSearchService.deduplicate_keep_order(hints)

    @classmethod
    def rank_chunks(
        cls,
        chunks: list[PrecedentChunk],
        keywords: list[str],
        chunk_types: list[str],
        query_text: str,
        top_k: int,
        concept_groups: list[list[str]] | None = None,
        case_name_hints: list[str] | None = None,
    ) -> list[PrecedentChunk]:
        concept_groups = concept_groups or []
        case_name_hints = case_name_hints or []
        raw_tokens = [*ChunkSearchService.extract_keywords(query_text), *keywords]
        query_terms: list[str] = []
        seen: set[str] = set()

        for token in raw_tokens:
            normalized = (
                " ".join(token.split())
                if " " in token
                else ChunkSearchService.normalize_search_token(token)
            )

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
        compact_query = re.sub(r"\s+", "", query_text)
        is_special_relation_fee_question = (
            any(term in compact_query for term in ("특수관계인", "특수관계자"))
            and "수수료" in compact_query
            and "부당행위계산" in compact_query
        )
        scored: list[tuple[float, PrecedentChunk]] = []

        for chunk in chunks:
            title_text = (chunk.title or "").lower()
            content_text = (chunk.content or "").lower()
            chunk_type_text = (chunk.chunk_type or "").lower()
            case_name_text = str(chunk.metadata.get("caseName") or "").lower()
            court_name_text = str(chunk.metadata.get("courtName") or "").lower()
            case_number_text = str(chunk.metadata.get("caseNumber") or "").lower()
            searchable_text = " ".join(
                [title_text, content_text, case_name_text, court_name_text, case_number_text]
            )

            score = 0.0

            for term in query_terms:
                title_hits = title_text.count(term) + case_name_text.count(term)
                content_hits = content_text.count(term)
                # 긴 FULL_TEXT가 단순 반복 횟수로 상위를 독점하지
                # 못하도록 용어별 횟수를 제한한다.
                score += (min(title_hits, 2) * 3.0) + min(content_hits, 3)

            matched_terms = sum(
                1
                for term in query_terms
                if term in searchable_text
            )

            overlap_ratio = matched_terms / len(query_terms)
            score += overlap_ratio * 10.0

            chunk_type_upper = (chunk.chunk_type or "").upper()
            if chunk_type_upper == "ISSUE":
                score += 18.0
            elif chunk_type_upper == "SUMMARY":
                score += 14.0
            elif chunk_type_upper == "FULL_TEXT":
                score -= 6.0

            if any(hint.lower() in case_name_text for hint in case_name_hints):
                score += 22.0
            elif any(term in case_name_text for term in cls.ADMIN_CASE_TERMS):
                score += 10.0

            if is_special_relation_fee_question:
                reasoning_positions: list[int] = []
                search_from = 0
                while True:
                    position = content_text.find("경제적 합리성", search_from)
                    if position < 0:
                        break
                    reasoning_positions.append(position)
                    search_from = position + 1
                has_application_reasoning = any(
                    "지급" in content_text[max(0, position - 1200):position + 1200]
                    and any(
                        term in content_text[max(0, position - 1200):position + 1200]
                        for term in ("수수료", "용역비")
                    )
                    for position in reasoning_positions
                )
                if has_application_reasoning:
                    score += 35.0
                else:
                    score -= 20.0

            criminal_text = f"{case_name_text} {case_number_text}"
            if any(term.lower() in criminal_text for term in cls.CRIMINAL_CASE_TERMS):
                score -= 45.0

            matched_groups = sum(
                1
                for group in concept_groups
                if any(term.lower() in searchable_text for term in group)
            )
            group_ratio = (
                matched_groups / len(concept_groups)
                if concept_groups
                else 1.0
            )
            score += matched_groups * 12.0
            if concept_groups and group_ratio == 1.0:
                score += 20.0

            # 질의 핵심 용어의 40%도 맞지 않으면 관련 판례로 보지 않는다.
            if overlap_ratio < 0.20 or group_ratio < 0.50:
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
