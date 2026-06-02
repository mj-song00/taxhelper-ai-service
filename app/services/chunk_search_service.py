from __future__ import annotations

import re

from app.schemas.retrieval import LawChunk, Pagination
from app.services.chunk_client import ChunkSearchClient


class ChunkSearchService:
    DOMAIN_TO_LAWS = {
        "소득세": ["소득세법", "소득세법 시행령", "소득세법 시행규칙"],
        "부가가치세": ["부가가치세법", "부가가치세법 시행령", "부가가치세법 시행규칙"],
        "법인세": ["법인세법", "법인세법 시행령", "법인세법 시행규칙"],
    }
    DOMAIN_HINTS = {
        "소득세": {
            "프리랜서",
            "사업소득",
            "근로소득",
            "종합소득세",
            "원천징수",
            "지급명세서",
            "인적공제",
            "연말정산",
        },
        "부가가치세": {
            "부가세",
            "부가가치세",
            "매입세액",
            "매출세액",
            "영세율",
            "세금계산서",
            "환급",
            "예정신고",
            "확정신고",
        },
        "법인세": {
            "법인",
            "접대비",
            "손금",
            "손금산입",
            "손금불산입",
            "익금",
            "감가상각",
            "법인세",
        },
    }
    TAX_KEYWORDS = {
        "소득세",
        "법인세",
        "부가가치세",
        "종합소득세",
        "원천징수",
        "세액공제",
        "세액감면",
        "가산세",
        "경정청구",
        "수정신고",
        "과세표준",
        "필요경비",
        "신고",
        "납부",
        "환급",
        "공제",
        "감면",
        "세무조사",
        "조세특례",
        "사업소득",
        "근로소득",
        "양도소득",
    }
    PARTICLE_SUFFIXES = (
        "으로부터",
        "에서",
        "으로",
        "에게",
        "한테",
        "까지",
        "부터",
        "처럼",
        "보다",
        "마저",
        "조차",
        "이라도",
        "라도",
        "이나",
        "나",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "의",
        "에",
        "와",
        "과",
        "도",
    )
    ENDING_SUFFIXES = (
        "인가요",
        "일까요",
        "할까요",
        "되나요",
        "됐나요",
        "합니다",
        "합니다만",
        "합니다요",
        "해요",
        "돼요",
        "인가",
        "일까",
        "하다",
        "하기",
        "하면",
        "하고",
        "해서",
        "되어",
        "된다",
    )
    VERB_STEMS = {
        "알리",
        "알려주",
        "보",
        "싶",
        "주",
        "되",
        "하",
        "맞",
        "가능하",
        "확인하",
        "찾",
        "문의하",
        "정리하",
        "설명하",
    }

    def __init__(self, client: ChunkSearchClient, candidate_size: int) -> None:
        self.client = client
        self.candidate_size = candidate_size

    async def retrieve_chunks(self, conditions: dict, top_k: int) -> tuple[list[LawChunk], Pagination]:
        # Spring returns broad candidates; Python reranks and keeps final top_k.
        raw_chunks, page_info = await self.client.fetch_chunks(
            candidate_page=1,
            candidate_size=self.candidate_size,
            law_names=conditions["law_names"],
            keywords=conditions["keywords"],
            rewritten_query=conditions["rewritten_query"],
        )
        chunks = [self._to_chunk(item) for item in raw_chunks]
        if conditions["law_names"]:
            chunks = self.prioritize_by_law_names(chunks, conditions["law_names"])
        # total_elements should represent filtered candidate pool size.
        page_info["total_elements"] = len(chunks)
        ranked = self.rank_chunks(
            chunks=chunks,
            keywords=conditions["keywords"],
            law_names=conditions["law_names"],
            query_text=conditions["original_question"],
            top_k=len(chunks),
        )
        ranked = ranked[:top_k]
        return ranked, Pagination(**page_info)

    def build_search_conditions(self, question: str) -> dict:
        normalized = " ".join(question.split())
        keywords = self.extract_keywords(normalized)
        tax_domain = self.infer_tax_domain(keywords)
        law_names = self.extract_law_names(normalized)
        if not law_names:
            law_names = self.infer_law_names(tax_domain)
        article_numbers = self.extract_article_numbers(normalized)
        action = self.detect_action(normalized)
        intent = self.detect_intent(normalized)
        rewritten_query = self.build_rewritten_query(
            question=normalized,
            intent=intent,
            action=action,
            keywords=keywords,
            law_names=law_names,
            article_numbers=article_numbers,
        )
        return {
            "intent": intent,
            "tax_domain": tax_domain,
            "action": action,
            "original_question": question,
            "keywords": keywords,
            "law_names": law_names,
            "article_numbers": article_numbers,
            "rewritten_query": rewritten_query,
        }

    @staticmethod
    def build_search_prompt(question: str, conditions: dict) -> str:
        return (
            "사용자 질문을 법령/판례 검색용 조건으로 변환한다.\n"
            f"- 원문 질문: {question}\n"
            f"- 검색 의도: {conditions['intent']}\n"
            f"- 세목: {conditions['tax_domain']}\n"
            f"- 행위: {conditions['action']}\n"
            f"- 법령명: {', '.join(conditions['law_names']) or '없음'}\n"
            f"- 조문 번호: {', '.join(conditions['article_numbers']) or '없음'}\n"
            f"- 핵심 키워드: {', '.join(conditions['keywords']) or '없음'}\n"
            f"- 검색 질의문: {conditions['rewritten_query']}"
        )

    def build_prompt_context(self, chunks: list[LawChunk]) -> str:
        if not chunks:
            return ""

        lines: list[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            source = " / ".join(
                value for value in [chunk.law_name, chunk.article, chunk.title] if value
            )
            header = f"[{idx}] {source}" if source else f"[{idx}]"
            lines.append(header)
            lines.append(chunk.content.strip())
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def extract_keywords(question: str) -> list[str]:
        tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", question)
        stop_words = {
            "사용자",
            "질문",
            "대한",
            "관련",
            "어떤",
            "어떻게",
            "경우",
            "가능",
            "있나요",
            "있을까요",
            "해주세요",
            "알려줘",
            "정리",
            "있다",
            "없다",
            "문의",
            "대해",
            "관련해",
            "대한지",
            "대상이",
            "기준",
        }
        seen: set[str] = set()
        general_keywords: list[str] = []
        tax_keywords: list[str] = []
        for token in tokens:
            normalized = ChunkSearchService.normalize_search_token(token)
            if len(normalized) < 2:
                continue
            if ChunkSearchService.is_unwanted_verb(normalized):
                continue
            lowered = normalized.lower()
            if lowered in stop_words:
                continue
            if lowered in seen:
                continue
            seen.add(lowered)
            if ChunkSearchService.is_tax_keyword(normalized):
                tax_keywords.append(normalized)
            else:
                general_keywords.append(normalized)
            if len(tax_keywords) + len(general_keywords) >= 8:
                break
        return (tax_keywords + general_keywords)[:8]

    @staticmethod
    def normalize_search_token(token: str) -> str:
        cleaned = re.sub(r"^[^0-9A-Za-z가-힣]+|[^0-9A-Za-z가-힣]+$", "", token)
        if len(cleaned) < 2:
            return ""

        normalized = cleaned
        for suffix in ChunkSearchService.PARTICLE_SUFFIXES:
            if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
                normalized = normalized[: -len(suffix)]
                break

        for suffix in ChunkSearchService.ENDING_SUFFIXES:
            if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
                normalized = normalized[: -len(suffix)]
                break

        # Morphological normalization for retrieval terms, e.g. "원천징수된" -> "원천징수"
        normalized = re.sub(r"(된|되는|되어|됐다|했던|하는|하고|하며|한)$", "", normalized)
        normalized = re.sub(r"(입니다|요|니다|까요|군요|네요)$", "", normalized)
        normalized = re.sub(r"(한다|했다|하며|해서|하면|하고|하는|되는|된다|됐다)$", "", normalized)
        return normalized.strip()

    @staticmethod
    def is_unwanted_verb(token: str) -> bool:
        lowered = token.lower()
        if lowered in ChunkSearchService.VERB_STEMS:
            return True
        return any(lowered.startswith(stem) for stem in ChunkSearchService.VERB_STEMS)

    @staticmethod
    def is_tax_keyword(token: str) -> bool:
        if token in ChunkSearchService.TAX_KEYWORDS:
            return True
        return any(keyword in token for keyword in ChunkSearchService.TAX_KEYWORDS)

    @staticmethod
    def infer_tax_domain(keywords: list[str]) -> str:
        domain_scores = {domain: 0 for domain in ChunkSearchService.DOMAIN_HINTS}
        for keyword in keywords:
            lowered = keyword.lower()
            for domain, hints in ChunkSearchService.DOMAIN_HINTS.items():
                for hint in hints:
                    if hint in keyword or hint.lower() in lowered:
                        domain_scores[domain] += 1

        best_domain = max(domain_scores, key=domain_scores.get)
        if domain_scores[best_domain] == 0:
            return "세무일반"
        return best_domain

    @staticmethod
    def infer_law_names(tax_domain: str) -> list[str]:
        return ChunkSearchService.DOMAIN_TO_LAWS.get(tax_domain, [])

    @staticmethod
    def detect_action(question: str) -> str:
        lowered = question.lower()
        if any(key in lowered for key in ("신고", "제출", "기한")):
            return "신고"
        if any(key in lowered for key in ("납부", "원천징수", "징수")):
            return "납부/징수"
        if any(key in lowered for key in ("환급", "경정청구")):
            return "환급/경정청구"
        if any(key in lowered for key in ("공제", "감면", "손금")):
            return "공제/감면"
        if any(key in lowered for key in ("가산세", "불이익", "처벌", "제재")):
            return "가산세/제재 검토"
        return "요건 검토"

    @staticmethod
    def extract_law_names(question: str) -> list[str]:
        matches = re.findall(r"[가-힣A-Za-z]+(?:법|시행령|시행규칙)", question)
        unique: list[str] = []
        seen: set[str] = set()
        for match in matches:
            if match in seen:
                continue
            seen.add(match)
            unique.append(match)
        return unique

    @staticmethod
    def extract_article_numbers(question: str) -> list[str]:
        patterns = re.findall(r"제\s*\d+\s*조(?:의\s*\d+)?", question)
        cleaned = [re.sub(r"\s+", "", item) for item in patterns]
        unique: list[str] = []
        seen: set[str] = set()
        for item in cleaned:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique

    @staticmethod
    def detect_intent(question: str) -> str:
        lowered = question.lower()
        if any(key in lowered for key in ("요건", "성립", "충족")):
            return "요건/적용 기준 확인"
        if any(key in lowered for key in ("신고", "납부", "기한", "절차")):
            return "신고/납부 절차 확인"
        if any(key in lowered for key in ("가산세", "불이익", "처벌", "제재")):
            return "위반 시 불이익 확인"
        return "세법 해석 및 적용 범위 확인"

    @staticmethod
    def build_rewritten_query(
        question: str,
        intent: str,
        action: str,
        keywords: list[str],
        law_names: list[str],
        article_numbers: list[str],
    ) -> str:
        del question, intent
        query_terms: list[str] = []
        query_terms.extend(law_names)
        query_terms.extend(article_numbers)
        query_terms.extend(keywords)
        query_terms.append(action)
        deduped_terms = ChunkSearchService.deduplicate_keep_order(query_terms)
        return " ".join(deduped_terms)

    @staticmethod
    def deduplicate_keep_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            result.append(value)
        return result

    @staticmethod
    def rank_chunks(
        chunks: list[LawChunk],
        keywords: list[str],
        law_names: list[str],
        query_text: str,
        top_k: int,
    ) -> list[LawChunk]:
        del keywords, law_names
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

        scored: list[tuple[float, LawChunk]] = []
        for chunk in chunks:
            title_text = (chunk.title or "").lower()
            content_text = (chunk.content or "").lower()
            score = 0.0
            for term in query_terms:
                title_hits = title_text.count(term)
                content_hits = content_text.count(term)
                score += (title_hits * 3.0) + (content_hits * 1.0)
            # Reward strong overlap ratio to improve semantic closeness.
            matched_terms = sum(
                1
                for term in query_terms
                if term in title_text or term in content_text
            )
            overlap_ratio = matched_terms / len(query_terms)
            score += overlap_ratio * 10.0
            chunk.score = round(score, 4)
            scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        ranked = [chunk for score, chunk in scored if score > 0]
        if len(ranked) < top_k:
            remainder = [chunk for score, chunk in scored if score == 0]
            ranked.extend(remainder[: top_k - len(ranked)])
        return ranked[:top_k]

    @staticmethod
    def prioritize_by_law_names(chunks: list[LawChunk], law_names: list[str]) -> list[LawChunk]:
        if not law_names:
            return chunks
        allowed_law_names = {
            re.sub(r"\s+", " ", name.strip()).lower()
            for name in law_names
            if name and name.strip()
        }
        matched: list[LawChunk] = []
        for chunk in chunks:
            chunk_law_name = re.sub(r"\s+", " ", (chunk.law_name or "").strip()).lower()
            if chunk_law_name in allowed_law_names:
                matched.append(chunk)
        return matched

    @staticmethod
    def _to_chunk(item: dict) -> LawChunk:
        chunk_id = item.get("chunkId") or item.get("chunk_id") or item.get("id") or ""
        content = (
            item.get("content")
            or item.get("chunk")
            or item.get("text")
            or item.get("body")
            or ""
        )
        law_name = (
            item.get("lawName")
            or item.get("law_name")
            or item.get("lawTitle")
            or item.get("title")
        )
        return LawChunk(
            chunk_id=str(chunk_id),
            law_id=item.get("lawId") or item.get("law_id") or item.get("lawID"),
            law_name=law_name,
            title=item.get("title"),
            article=item.get("article") or item.get("articleNo") or item.get("article_no"),
            content=str(content),
            score=item.get("score"),
            metadata=item.get("metadata") or {},
        )
