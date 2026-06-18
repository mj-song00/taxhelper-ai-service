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
            "프리랜서", "사업소득", "근로소득", "종합소득세", "원천징수",
            "지급명세서", "인적공제", "연말정산", "인적용역",
            "교육비", "세액공제", "특별세액공제",
            "장기저당차입금", "장기주택저당차입금", "이자상환액", "주택자금", "주택자금공제", "특별소득공제",
            "해외주식", "미국주식", "외국주식", "국외주식", "주식", "양도소득", "양도소득세",
            "매매차익", "양도차익", "배당소득", "금융소득", "국외자산",
        },
        "부가가치세": {
            "부가세", "부가가치세", "매입세액", "매출세액", "영세율",
            "세금계산서", "환급", "예정신고", "확정신고",
        },
        "법인세": {
            "법인", "접대비", "손금", "손금산입", "손금불산입",
            "익금", "감가상각", "법인세", "대손충당금", "대손금",
            "대손", "채권", "구상채권", "가지급금", "채무보증",
        },
    }

    TAX_KEYWORDS = {
        "소득세", "법인세", "부가가치세", "종합소득세", "원천징수",
        "세액공제", "세액감면", "가산세", "경정청구", "수정신고",
        "과세표준", "필요경비", "신고", "납부", "환급", "공제",
        "감면", "세무조사", "조세특례", "사업소득", "근로소득",
        "양도소득", "인적용역", "지급명세서", "확정신고",
        "장기저당차입금", "장기주택저당차입금", "이자상환액", "주택자금", "주택자금공제", "특별소득공제",
        "대손충당금", "대손금", "대손", "채권", "구상채권", "가지급금", "채무보증",
        "해외주식", "미국주식", "외국주식", "국외주식", "주식", "매매차익", "양도차익",
        "배당소득", "금융소득", "국외자산", "양도소득세",
    }

    TAX_SYNONYMS = {
        "이자상환액": [
            "이자",
            "상환",
            "차입금의 이자",
            "공제한도",
            "공제 한도",
            "한도액",
            "장기주택저당차입금의 이자",
        ],
        "주담대": [
            "주택담보대출",
            "주택저당차입금",
            "장기주택저당차입금",
        ],
        "장기저당차입금": [
            "장기주택저당차입금",
        ],
        "장기주택저당차입금": [
            "주택자금공제",
            "특별소득공제",
            "소득공제",
        ],
        "한도": [
            "한도액",
        ],
        "대손충당금": [
            "대손금",
            "손금산입",
            "설정대상채권",
            "채권잔액",
            "제34조",
            "제19조의2",
            "제61조",
            "채무보증",
            "구상채권",
            "특수관계인",
            "업무와 관련 없이",
            "가지급금",
        ],
        "부채": [
            "채권",
            "구상채권",
            "가지급금",
        ],
        "부채들": [
            "채권",
            "구상채권",
            "가지급금",
        ],
        "제외": [
            "제외",
            "제외한다",
            "제외되는",
        ],
    }

    HOUSING_LOAN_TERMS = {
        "장기주택저당차입금", "장기저당차입금", "주택저당차입금",
        "주택담보대출", "주담대", "이자상환액", "주택자금공제",
    }

    HOUSING_LOAN_QUERY_HINTS = [
        "장기주택저당차입금",
        "이자상환액",
        "한도액",
        "공제한도",
        "특별소득공제",
        "주택자금공제",
        "제52조",
        "제112조",
        "상환기간",
        "고정금리",
        "비거치식",
        "분할상환",
    ]

    BAD_DEBT_ALLOWANCE_TERMS = {
        "대손충당금", "대손금", "대손", "채권", "구상채권", "가지급금", "채무보증",
    }

    BAD_DEBT_ALLOWANCE_QUERY_HINTS = [
        "대손충당금",
        "대손금",
        "손금산입",
        "설정대상채권",
        "채권잔액",
        "채무보증",
        "구상채권",
        "특수관계인",
        "업무와 관련 없이",
        "업무무관",
        "가지급금",
        "제34조",
        "제19조의2",
        "제61조",
    ]

    BUSINESS_WITHHOLDING_TERMS = {
        "3.3", "3.3%", "프리랜서", "사업소득", "원천징수", "종합소득세", "종소세",
    }

    BUSINESS_WITHHOLDING_QUERY_HINTS = [
        "제70조",
        "제73조",
        "제127조",
        "제137조",
        "종합소득",
        "과세표준확정신고",
        "사업소득",
        "원천징수",
        "확정신고",
        "신고하지 아니할 수 있다",
    ]

    OVERSEAS_STOCK_TERMS = {
        "미국 주식", "미국주식", "해외 주식", "해외주식", "외국 주식", "외국주식",
        "국외 주식", "국외주식", "매매차익", "양도차익", "양도소득", "양도소득세",
        "배당금", "배당소득", "금융소득",
    }

    OVERSEAS_STOCK_QUERY_HINTS = [
        "국외자산",
        "국외전출자",
        "양도소득",
        "양도소득세",
        "주식등",
        "외국법인",
        "양도차익",
        "양도소득기본공제",
        "250만원",
        "제94조",
        "제104조",
        "제105조",
        "제110조",
        "제118조",
        "배당소득",
        "금융소득",
        "이자소득",
        "2천만원",
        "종합소득",
    ]

    PARTICLE_SUFFIXES = (
        "으로부터", "에서", "으로", "에게", "한테", "까지", "부터",
        "처럼", "보다", "마저", "조차", "이라도", "라도", "이나",
        "나", "은", "는", "이", "가", "을", "를", "의", "에",
        "와", "과", "도",
    )

    ENDING_SUFFIXES = (
        "인가요", "일까요", "할까요", "되나요", "됐나요", "합니다",
        "합니다만", "합니다요", "해요", "돼요", "인가", "일까",
        "하다", "하기", "하면", "하고", "해서", "되어", "된다",
    )

    VERB_STEMS = {
        "알리", "알려주", "보", "싶", "주", "되", "하", "맞",
        "가능하", "확인하", "찾", "문의하", "정리하", "설명하",
    }

    def __init__(self, client: ChunkSearchClient, candidate_size: int) -> None:
        self.client = client
        self.candidate_size = candidate_size

    def build_search_conditions(self, question: str) -> dict:
        keywords = self.extract_keywords(question)

        expanded_keywords = []

        for keyword in keywords:
            if keyword not in expanded_keywords:
                expanded_keywords.append(keyword)

            for synonym in self.TAX_SYNONYMS.get(keyword, []):
                if synonym not in expanded_keywords:
                    expanded_keywords.append(synonym)

        if self.is_housing_loan_question(question, expanded_keywords):
            expanded_keywords = self.prepend_unique(
                self.HOUSING_LOAN_QUERY_HINTS,
                expanded_keywords,
            )

        if self.is_bad_debt_allowance_question(question, expanded_keywords):
            expanded_keywords = self.prepend_unique(
                self.BAD_DEBT_ALLOWANCE_QUERY_HINTS,
                expanded_keywords,
            )

        if self.is_business_withholding_question(question, expanded_keywords):
            expanded_keywords = self.prepend_unique(
                self.BUSINESS_WITHHOLDING_QUERY_HINTS,
                expanded_keywords,
            )

        if self.is_overseas_stock_question(question, expanded_keywords):
            expanded_keywords = self.prepend_unique(
                self.OVERSEAS_STOCK_QUERY_HINTS,
                expanded_keywords,
            )

        keywords = expanded_keywords[:20]

        tax_domain = self.infer_tax_domain(keywords)

        law_names = self.extract_law_names(question)
        if not law_names:
            law_names = self.infer_law_names(tax_domain)

        article_numbers = self.extract_article_numbers(question)
        intent = self.detect_intent(question)
        action = self.detect_action(question)

        rewritten_query = self.build_rewritten_query(
            question=question,
            intent=intent,
            action=action,
            keywords=keywords,
            law_names=law_names,
            article_numbers=article_numbers,
        )

        return {
            "original_question": question,
            "intent": intent,
            "tax_domain": tax_domain,
            "action": action,
            "keywords": keywords,
            "law_names": law_names,
            "article_numbers": article_numbers,
            "rewritten_query": rewritten_query,
        }

    async def retrieve_chunks(
        self,
        conditions: dict,
        top_k: int,
    ) -> tuple[list[LawChunk], Pagination]:
        top_k = max(1, min(int(top_k), 20))

        print("keywords =", conditions["keywords"])
        print("query =", conditions["rewritten_query"])
        print("law_names =", conditions["law_names"])


        raw_chunks, page_info = await self.client.fetch_chunks(
            candidate_page=1,
            candidate_size=self.candidate_size,
            law_names=conditions["law_names"],
            keywords=conditions["keywords"],
            rewritten_query=conditions["rewritten_query"],
        )

        chunks = [self._to_chunk(item) for item in raw_chunks]

        for supplemental_conditions in self.build_supplemental_conditions(conditions):
            supplemental_raw_chunks, _ = await self.client.fetch_chunks(
                candidate_page=1,
                candidate_size=self.candidate_size,
                law_names=supplemental_conditions["law_names"],
                keywords=supplemental_conditions["keywords"],
                rewritten_query=supplemental_conditions["rewritten_query"],
            )
            chunks.extend(self._to_chunk(item) for item in supplemental_raw_chunks)

        chunks = self.deduplicate_chunks(chunks)

        print("========== SEARCH RESULT ==========")
        for idx, chunk in enumerate(chunks, start=1):
            print(
            f"{idx}. score={chunk.score} "
            f"law={chunk.law_name} "
            f"title={chunk.title}"
            )
        print("========== SEARCH RESULT END ==========")

        if conditions["law_names"]:
            chunks = self.prioritize_by_law_names(chunks, conditions["law_names"])

        ranked = self.rank_chunks(
            chunks=chunks,
            keywords=conditions["keywords"],
            law_names=conditions["law_names"],
            query_text=conditions["original_question"],
            top_k=top_k,
        )

        page_info["total_elements"] = len(ranked)

        print("========== RANKED RESULT ==========")
        for idx, chunk in enumerate(ranked[:20], start=1):
            print(
            f"{idx}. score={chunk.score} "
            f"law={chunk.law_name} "
            f"title={chunk.title}"
        )
        print("========== RANKED RESULT END ==========")

        return ranked, Pagination(**page_info)

    @staticmethod
    def build_prompt_context(chunks: list[LawChunk], max_content_chars: int = 1200) -> str:
        if not chunks:
            return "검색된 법령 근거가 없습니다."

        contexts: list[str] = []

        for index, chunk in enumerate(chunks, start=1):
            law_name = chunk.law_name or "법령명 없음"
            title = chunk.title or chunk.article or "제목 없음"
            content = ChunkSearchService.truncate_text(
                chunk.content or "",
                max_content_chars,
            )

            contexts.append(
                f"[{index}]\n"
                f"법령명: {law_name}\n"
                f"조문명: {title}\n"
                f"내용:\n{content}"
            )

        return "\n\n".join(contexts)

    @staticmethod
    def truncate_text(text: str, max_chars: int) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()

        if len(normalized) <= max_chars:
            return normalized

        return normalized[:max_chars].rstrip() + "..."


    @classmethod
    def is_housing_loan_question(
        cls,
        question: str,
        keywords: list[str] | None = None,
    ) -> bool:
        search_text = " ".join([question, *(keywords or [])])

        return any(term in search_text for term in cls.HOUSING_LOAN_TERMS)

    @classmethod
    def is_bad_debt_allowance_question(
        cls,
        question: str,
        keywords: list[str] | None = None,
    ) -> bool:
        search_text = " ".join([question, *(keywords or [])])

        return any(term in search_text for term in cls.BAD_DEBT_ALLOWANCE_TERMS)

    @classmethod
    def is_business_withholding_question(
        cls,
        question: str,
        keywords: list[str] | None = None,
    ) -> bool:
        search_text = " ".join([question, *(keywords or [])])

        return (
            any(term in search_text for term in cls.BUSINESS_WITHHOLDING_TERMS)
            and any(term in search_text for term in ("신고", "종합소득세", "종소세"))
        )

    @classmethod
    def is_overseas_stock_question(
        cls,
        question: str,
        keywords: list[str] | None = None,
    ) -> bool:
        search_text = " ".join([question, *(keywords or [])])
        compact_text = search_text.replace(" ", "")

        return (
            any(term.replace(" ", "") in compact_text for term in cls.OVERSEAS_STOCK_TERMS)
            or ("미국" in search_text and "주식" in search_text)
            or ("해외" in search_text and "주식" in search_text)
        )

    @staticmethod
    def prepend_unique(priority_values: list[str], values: list[str]) -> list[str]:
        return ChunkSearchService.deduplicate_keep_order(priority_values + values)

    @staticmethod
    def deduplicate_chunks(chunks: list[LawChunk]) -> list[LawChunk]:
        result: list[LawChunk] = []
        seen: set[str] = set()

        for chunk in chunks:
            key = chunk.chunk_id or f"{chunk.law_name}:{chunk.title}:{chunk.content[:80]}"

            if key in seen:
                continue

            seen.add(key)
            result.append(chunk)

        return result

    @classmethod
    def build_supplemental_conditions(cls, conditions: dict) -> list[dict]:
        supplemental_conditions: list[dict] = []

        if cls.is_housing_loan_question(
            conditions["original_question"],
            conditions["keywords"],
        ):
            law_names = cls.prepend_unique(
                ["소득세법", "소득세법 시행령"],
                conditions["law_names"],
            )
            keywords = cls.prepend_unique(
                cls.HOUSING_LOAN_QUERY_HINTS,
                conditions["keywords"],
            )
            supplemental_conditions.extend([
                {
                    "law_names": law_names,
                    "keywords": keywords,
                    "rewritten_query": " ".join(
                        cls.deduplicate_keep_order(law_names + keywords)
                    ),
                },
                {
                    "law_names": law_names,
                    "keywords": [
                        "제52조",
                        "제112조",
                        "장기주택저당차입금",
                        "한도액",
                        "이자상환액",
                        "상환기간",
                        "고정금리",
                        "비거치식",
                        "분할상환",
                    ],
                    "rewritten_query": (
                        "소득세법 제52조 소득세법 시행령 제112조 "
                        "장기주택저당차입금 이자상환액 한도액 "
                        "상환기간 고정금리 비거치식 분할상환"
                    ),
                },
            ])

        if cls.is_bad_debt_allowance_question(
            conditions["original_question"],
            conditions["keywords"],
        ):
            law_names = cls.prepend_unique(
                ["법인세법", "법인세법 시행령"],
                conditions["law_names"],
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": cls.prepend_unique(
                        cls.BAD_DEBT_ALLOWANCE_QUERY_HINTS,
                        conditions["keywords"],
                    ),
                    "rewritten_query": (
                        "법인세법 제34조 제19조의2 법인세법 시행령 제61조 "
                        "대손충당금 손금산입 설정대상채권 채권잔액 "
                        "채무보증 구상채권 특수관계인 업무와 관련 없이 가지급금 제외"
                    ),
                }
            )

        if cls.is_business_withholding_question(
            conditions["original_question"],
            conditions["keywords"],
        ):
            law_names = cls.prepend_unique(
                ["소득세법", "소득세법 시행령"],
                conditions["law_names"],
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": cls.prepend_unique(
                        cls.BUSINESS_WITHHOLDING_QUERY_HINTS,
                        conditions["keywords"],
                    ),
                    "rewritten_query": (
                        "소득세법 제70조 제73조 제127조 소득세법 시행령 제137조 "
                        "종합소득 과세표준확정신고 사업소득 원천징수 확정신고 예외"
                    ),
                }
            )

        if cls.is_overseas_stock_question(
            conditions["original_question"],
            conditions["keywords"],
        ):
            law_names = cls.prepend_unique(
                ["소득세법", "소득세법 시행령"],
                conditions["law_names"],
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": cls.prepend_unique(
                        cls.OVERSEAS_STOCK_QUERY_HINTS,
                        conditions["keywords"],
                    ),
                    "rewritten_query": (
                        "소득세법 제94조 제104조 제105조 제110조 제118조 "
                        "국외자산 외국법인 주식등 양도소득 양도소득세 "
                        "양도차익 양도소득기본공제 250만원 배당소득 금융소득 2천만원"
                    ),
                }
            )

        return supplemental_conditions

    @staticmethod
    def extract_keywords(question: str) -> list[str]:
        tokens = re.findall(r"[0-9A-Za-z가-힣.%]{2,}", question)

        stop_words = {
            "사용자", "질문", "대한", "관련", "어떤", "어떻게", "경우",
            "가능", "있나요", "있을까요", "해주세요", "알려줘", "정리",
            "있다", "없다", "문의", "대해", "관련해", "대한지",
            "대상이", "기준", "받으면", "받은경우", "되나요",
            "세금", "내야", "하나요", "나요", "수익",
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
        cleaned = re.sub(
            r"^[^0-9A-Za-z가-힣.%]+|[^0-9A-Za-z가-힣.%]+$",
            "",
            token,
        )

        if len(cleaned) < 2:
            return ""

        if re.fullmatch(r"\d+\.\d+%", cleaned):
            return cleaned

        normalized = cleaned

        for suffix in ChunkSearchService.PARTICLE_SUFFIXES:
            if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
                normalized = normalized[: -len(suffix)]
                break

        for suffix in ChunkSearchService.ENDING_SUFFIXES:
            if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
                normalized = normalized[: -len(suffix)]
                break

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
        del law_names

        raw_tokens = re.findall(r"[0-9A-Za-z가-힣.%]{2,}", query_text)

        query_terms: list[str] = []
        seen: set[str] = set()


        for token in raw_tokens + keywords:
            normalized = ChunkSearchService.normalize_search_token(token)

            if len(normalized) < 2:
                continue

            if ChunkSearchService.is_unwanted_verb(normalized):
                continue

            lowered = normalized.lower()

            if lowered in seen:
                continue

            seen.add(lowered)
            query_terms.append(lowered)

        print("query_terms =", query_terms)
    

        if not query_terms:
            for chunk in chunks:
                chunk.score = 0.0

            return chunks[:top_k]

        has_three_percent = "3.3%" in query_text or "3.3" in query_text
        has_business_income = "사업소득" in keywords
        has_withholding = "원천징수" in keywords

        has_housing_loan = ChunkSearchService.is_housing_loan_question(
            query_text,
            keywords,
        )
        has_bad_debt_allowance = ChunkSearchService.is_bad_debt_allowance_question(
            query_text,
            keywords,
        )
        has_business_withholding = ChunkSearchService.is_business_withholding_question(
            query_text,
            keywords,
        )
        has_overseas_stock = ChunkSearchService.is_overseas_stock_question(
            query_text,
            keywords,
        )

        scored: list[tuple[float, LawChunk]] = []

        for chunk in chunks:
            title_text = (chunk.title or "").lower()
            content_text = (chunk.content or "").lower()
            chunk_type = (chunk.chunk_type or "").upper()

            score = 0.0

            for term in query_terms:
                title_hits = title_text.count(term)
                content_hits = content_text.count(term)

                score += (title_hits * 3.0) + content_hits

            if has_three_percent or has_business_income or has_withholding:
                if "이자소득" in title_text or "배당소득" in title_text:
                    score -= 8.0

                if "사업소득" in title_text or "사업소득" in content_text:
                    score += 8.0

                if "원천징수" in title_text or "원천징수" in content_text:
                    score += 8.0

                if "인적용역" in title_text or "인적용역" in content_text:
                    score += 5.0

            if has_bad_debt_allowance:
                combined_text = f"{title_text} {content_text}"
                target_article = (
                    "제34조" in title_text
                    or "제61조" in title_text
                    or "제19조의2" in title_text
                    or "제19조의2" in content_text
                )
                direct_terms = (
                    "대손충당금" in combined_text
                    or "대손금" in combined_text
                    or "구상채권" in combined_text
                    or "가지급금" in combined_text
                    or "채무보증" in combined_text
                    or "특수관계인" in combined_text
                    or "업무와 관련 없이" in combined_text
                    or "업무무관" in combined_text
                    or "설정대상채권" in combined_text
                )

                if not (target_article or direct_terms):
                    chunk.score = 0.0
                    continue

                if "외국납부세액" in combined_text or "외국법인세액" in combined_text:
                    chunk.score = 0.0
                    continue

                if target_article:
                    score += 55.0

                if "대손충당금" in combined_text:
                    score += 45.0

                if "구상채권" in combined_text or "가지급금" in combined_text or "채무보증" in combined_text:
                    score += 40.0

                if "특수관계인" in combined_text or "업무와 관련 없이" in combined_text or "업무무관" in combined_text:
                    score += 35.0

                if "손금산입" in combined_text or "채권잔액" in combined_text or "설정대상채권" in combined_text:
                    score += 18.0

            if has_business_withholding:
                combined_text = f"{title_text} {content_text}"
                target_article = (
                    "제70조" in title_text
                    or "제73조" in title_text
                    or "제127조" in title_text
                    or "제137조" in title_text
                )
                direct_terms = (
                    "종합소득" in combined_text
                    or "과세표준확정신고" in combined_text
                    or "사업소득" in combined_text
                    or "원천징수" in combined_text
                )

                if not (target_article or direct_terms):
                    chunk.score = 0.0
                    continue

                if target_article:
                    score += 60.0

                if "제70조" in title_text or "종합소득 과세표준확정신고" in combined_text:
                    score += 40.0

                if "제73조" in title_text or "확정신고의 예외" in combined_text:
                    score += 35.0

                if "제137조" in title_text or "대통령령으로 정하는 사업소득" in combined_text:
                    score += 30.0

                if "사업소득" in combined_text and "원천징수" in combined_text:
                    score += 25.0

            if has_overseas_stock:
                combined_text = f"{title_text} {content_text}"
                target_article = (
                    "제94조" in title_text
                    or "제104조" in title_text
                    or "제105조" in title_text
                    or "제110조" in title_text
                    or "제118조" in title_text
                )
                direct_terms = (
                    "국외자산" in combined_text
                    or "외국법인" in combined_text
                    or "주식등" in combined_text
                    or "주식 등" in combined_text
                    or "양도소득" in combined_text
                    or "양도소득기본공제" in combined_text
                    or "배당소득" in combined_text
                    or "금융소득" in combined_text
                )

                if not (target_article or direct_terms):
                    chunk.score = 0.0
                    continue

                if target_article:
                    score += 55.0

                if "국외자산" in combined_text or "외국법인" in combined_text:
                    score += 45.0

                if "주식등" in combined_text or "주식 등" in combined_text:
                    score += 40.0

                if "양도소득" in combined_text or "양도차익" in combined_text:
                    score += 35.0

                if "250만원" in combined_text or "양도소득기본공제" in combined_text:
                    score += 30.0

                if "배당소득" in combined_text or "금융소득" in combined_text:
                    score += 25.0

            if has_housing_loan:
                if chunk_type == "ARTICLE":
                    score += 20.0
                elif chunk_type == "SUPPLEMENT":
                    score -= 80.0
                elif chunk_type == "AMENDMENT":
                    score -= 50.0

                has_direct_housing_term = (
                    "장기주택저당차입금" in title_text
                    or "장기주택저당차입금" in content_text
                    or "장기저당차입금" in title_text
                    or "장기저당차입금" in content_text
                    or "주택저당차입금" in title_text
                    or "주택저당차입금" in content_text
                    or "이자상환액" in title_text
                    or "이자상환액" in content_text
                    or "주택자금공제" in title_text
                    or "주택자금공제" in content_text
                )
                has_target_article = (
                    "제52조" in title_text
                    or "제112조" in title_text
                )

                if not (has_direct_housing_term or has_target_article):
                    chunk.score = 0.0
                    continue

                if "제52조" in title_text or "특별소득공제" in title_text:
                    score += 20.0

                if "제112조" in title_text or "주택자금공제" in title_text:
                    score += 25.0

                if "장기주택저당차입금" in content_text:
                    score += 20.0

                if "한도액" in content_text or "공제한도" in content_text or "공제 한도" in content_text:
                    score += 18.0

                if "상환기간" in content_text:
                    score += 8.0

                if "고정금리" in content_text or "비거치식" in content_text or "분할상환" in content_text:
                    score += 8.0

            matched_terms = sum(
                1
                for term in query_terms
                if term in title_text or term in content_text
            )

            overlap_ratio = matched_terms / len(query_terms)
            score += overlap_ratio * 2.0

            chunk.score = round(score, 4)
            scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)

        ranked = [
            chunk
            for score, chunk in scored
            if score > 0 and not ChunkSearchService.is_deleted_chunk(chunk)
        ]

        return ranked[:top_k]

    @staticmethod
    def is_deleted_chunk(chunk: LawChunk) -> bool:
        title = re.sub(r"\s+", "", chunk.title or "")
        content = re.sub(r"\s+", "", chunk.content or "")

        return (
            title.endswith("삭제")
            or content == "삭제"
            or re.fullmatch(r"제\d+조.*삭제", content) is not None
        )

    @staticmethod
    def prioritize_by_law_names(
        chunks: list[LawChunk],
        law_names: list[str],
    ) -> list[LawChunk]:
        if not law_names:
            return chunks

        allowed_law_names = {
            re.sub(r"\s+", " ", name.strip()).lower()
            for name in law_names
            if name and name.strip()
        }

        matched: list[LawChunk] = []

        for chunk in chunks:
            chunk_law_name = re.sub(
                r"\s+",
                " ",
                (chunk.law_name or "").strip(),
            ).lower()

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
            or ""
        )

        return LawChunk(
            chunk_id=str(chunk_id),
            law_id=item.get("lawId") or item.get("law_id") or item.get("lawID"),
            law_name=law_name,
            chunk_type=item.get("chunkType") or item.get("chunk_type"),
            title=item.get("title"),
            article=item.get("article") or item.get("articleNo") or item.get("article_no"),
            content=str(content),
            score=item.get("score"),
            metadata=item.get("metadata") or {},
        )
