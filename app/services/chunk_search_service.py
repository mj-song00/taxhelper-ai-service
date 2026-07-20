from __future__ import annotations

import asyncio
import re

from app.schemas.retrieval import LawChunk, Pagination
from app.services.chunk_client import ChunkSearchClient
from app.services.embedding_service import EmbeddingService
from app.services.legal_concepts import (
    build_weighted_terms,
    extract_legal_concepts,
    infer_required_roles,
)


class ChunkSearchService:
    DOMAIN_TO_LAWS = {
        "소득세": ["소득세법", "소득세법 시행령", "소득세법 시행규칙"],
        "부가가치세": ["부가가치세법", "부가가치세법 시행령", "부가가치세법 시행규칙"],
        "법인세": ["법인세법", "법인세법 시행령", "법인세법 시행규칙"],
        "개별소비세": ["개별소비세법", "개별소비세법 시행령", "개별소비세법 시행규칙"],
        "지방세": [
            "지방세법",
            "지방세법 시행령",
            "지방세특례제한법",
            "지방세특례제한법 시행령",
        ],
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
        "개별소비세": {
            "개별소비세", "개별 소비세", "개소세", "유류세", "유종",
            "유종별", "석유류", "휘발유", "경유", "등유", "중유", "프로판",
            "부탄", "액화석유가스", "LPG", "천연가스", "유연탄", "세율",
        },
        "지방세": {
            "지방세", "취득세", "재산세", "등록면허세", "지방세감면",
            "지방세특례", "감면세액", "추징", "유예기간", "직접사용",
        },
    }

    TAX_KEYWORDS = {
        "소득세", "법인세", "부가가치세", "종합소득세", "원천징수",
        "세액공제", "세액감면", "가산세", "경정청구", "수정신고",
        "과세표준", "필요경비", "신고", "납부", "환급", "공제",
        "감면", "세무조사", "조세특례", "사업소득", "근로소득",
        "폐업", "재고", "잔존재화", "자기생산ㆍ취득재화", "사업 개시일",
        "양도소득", "인적용역", "지급명세서", "확정신고",
        "개별소비세", "개소세", "유류세", "석유류", "유종별",
        "휘발유", "경유", "등유", "중유", "프로판", "부탄", "세율",
        "장기저당차입금", "장기주택저당차입금", "이자상환액", "주택자금", "주택자금공제", "특별소득공제",
        "대손충당금", "대손금", "대손", "채권", "구상채권", "가지급금", "채무보증",
        "해외주식", "미국주식", "외국주식", "국외주식", "주식", "매매차익", "양도차익",
        "배당소득", "금융소득", "국외자산", "양도소득세",
        "지방세", "취득세", "재산세", "등록면허세", "지방세감면",
        "지방세특례", "감면세액", "추징", "유예기간", "직접사용",
    }

    TAX_SYNONYMS = {
        "공급가액": ["부가가치세 포함", "110분의 100", "110분의 10", "제29조"],
        "부가가치세 포함": ["공급가액", "110분의 100", "제29조"],
        "임대료": ["공급가액", "부가가치세 포함", "제29조"],
        "월세": ["받은 대가", "공급가액", "공급대가", "110분의 100", "부가가치세 포함", "제29조"],
        "부동산 임대": ["받은 대가", "공급가액", "공급대가", "110분의 100", "부가가치세 포함", "제29조"],
        "리스": ["자동차의 구입과 임차", "비영업용 소형승용차", "경형승용자동차", "제39조", "제78조"],
        "사업용 차량": ["자동차의 구입과 임차", "비영업용 소형승용차", "운수업", "자동차판매업", "제39조", "제78조"],
        "차량": ["자동차의 구입과 임차", "비영업용 소형승용차", "경형승용자동차", "제39조", "제78조"],
        "폐업": ["남아 있는 재화", "잔존재화", "자기생산ㆍ취득재화", "제10조"],
        "재고": ["남아 있는 재화", "잔존재화", "자기생산ㆍ취득재화", "폐업"],
        "유종별": ["석유류", "휘발유", "경유", "등유", "중유", "프로판", "부탄"],
        "유종": ["석유류", "휘발유", "경유", "등유", "중유", "프로판", "부탄"],
        "석유류": ["휘발유", "경유", "등유", "중유", "석유가스", "프로판", "부탄"],
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

    EXEMPT_INVOICE_PENALTY_QUERY_HINTS = [
        "면세사업자 계산서 발급",
        "계산서 미발급",
        "지연발급",
        "계산서합계표",
        "공급가액의 100분의 2",
        "공급가액의 100분의 1",
        "공급가액의 1천분의 5",
        "제163조",
        "제81조의10",
        "제121조",
        "제75조의8",
    ]

    CLOSING_INVENTORY_TERMS = {
        "폐업", "폐업할 때", "남아 있는 재화", "잔존재화", "자기생산ㆍ취득재화",
        "사업 개시일 이전", "사실상 사업을 시작하지",
    }

    SIMPLE_TAXPAYER_INVOICE_QUERY_HINTS = [
        "간이과세자 세금계산서 발급",
        "영수증 발급",
        "직전 연도 공급대가",
        "4천800만원",
        "1억4백만원",
        "세금계산서 미발급 가산세",
        "제32조",
        "제36조",
        "제36조의2",
        "제61조",
        "제68조의2",
        "제109조",
    ]

    RECOGNIZED_INTEREST_QUERY_HINTS = [
        "가지급금 인정이자",
        "금전의 대여",
        "특수관계인",
        "부당행위계산의 부인",
        "시가",
        "가중평균차입이자율",
        "당좌대출이자율",
        "가지급금 적수",
        "365",
        "제52조",
        "제88조",
        "제89조",
    ]

    SPECIAL_RELATION_FEE_TERMS = {
        "특수관계인", "특수관계자", "계열회사", "수수료", "용역비",
        "필요경비", "손금", "부당행위계산", "부당행위계산부인",
    }

    SPECIAL_RELATION_FEE_QUERY_HINTS = [
        "부당행위계산의 부인",
        "특수관계인",
        "특수관계인으로부터",
        "높은 이율",
        "제공받는 경우",
        "경제적 합리성",
        "건전한 사회통념",
        "상관행",
        "시가",
        "용역",
        "수수료",
        "필요경비",
        "제98조",
    ]

    INCOME_SPLITTING_QUERY_HINTS = [
        "부당행위계산의 부인",
        "조세 부담을 부당하게 감소",
        "특수관계인",
        "법인 설립",
        "소득 분산",
        "실질적인 용역",
        "경제적 합리성",
        "건전한 사회통념",
        "상관행",
        "시가",
        "제41조",
        "제98조",
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

    PETROLEUM_TAX_TERMS = {
        "유종", "유종별", "석유류", "유류", "휘발유", "경유", "등유",
        "중유", "석유가스", "액화석유가스", "lpg", "프로판", "부탄",
    }

    PETROLEUM_TAX_QUERY_HINTS = [
        "개별소비세법", "제1조", "과세대상과 세율", "석유류", "수량",
        "리터당", "킬로그램당", "휘발유", "경유", "등유", "중유",
        "프로판", "부탄", "천연가스", "유연탄",
    ]

    PRE_REGISTRATION_INPUT_TAX_QUERY_HINTS = [
        "부가가치세법", "제39조", "제1항제8호", "공제하지 아니하는 매입세액",
        "사업자등록을 신청하기 전", "공급시기", "과세기간", "20일 이내",
        "등록신청일", "과세기간 기산일",
    ]

    PARTICLE_SUFFIXES = (
        "으로부터", "에서", "으로", "에게", "한테", "까지", "부터",
        "처럼", "보다", "마저", "조차", "이라도", "라도", "이나",
        "나", "은", "는", "이", "가", "을", "를", "의", "에",
        "와", "과", "도",
        "로",
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

    def __init__(
        self,
        client: ChunkSearchClient,
        candidate_size: int,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.client = client
        self.candidate_size = candidate_size
        self.embedding_service = embedding_service

    def build_search_conditions(self, question: str) -> dict:
        keywords = self.extract_keywords(question)
        if self.is_closing_inventory_question(question, keywords):
            keywords = self.prepend_unique(
                ["폐업", "잔존재화", "남아 있는 재화", "자기생산ㆍ취득재화"],
                keywords,
            )
        normalized_question = re.sub(r"\s+", "", question)
        if any(term in normalized_question for term in ("공급가액", "부가가치세포함", "임대료", "월세", "부동산임대", "받은금액", "받은대가", "공급대가")):
            keywords = self.prepend_unique(
                ["공급가액", "받은 대가", "공급대가", "부가가치세 포함 여부 불분명", "110분의 100", "110분의 10", "부가가치세법 제29조"],
                keywords,
            )
        if any(term in normalized_question for term in ("리스", "사업용차", "사업용자동차", "차량", "승용차", "화물차")):
            keywords = self.prepend_unique(
                ["자동차의 구입과 임차", "비영업용 소형승용차", "경형승용자동차", "운수업", "자동차판매업", "제39조", "제78조"],
                keywords,
            )
        concepts = extract_legal_concepts(question)
        keyword_weights = build_weighted_terms(concepts)
        required_roles = infer_required_roles(concepts)

        # 사용자가 `개별 소비세`로 띄어 써도 법령명은 하나의
        # 검색어로 보존한다.
        if "개별소비세" in question.replace(" ", ""):
            keywords = self.prepend_unique(["개별소비세"], keywords)

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

        if self.is_exempt_invoice_penalty_question(question):
            expanded_keywords = self.prepend_unique(
                self.EXEMPT_INVOICE_PENALTY_QUERY_HINTS,
                expanded_keywords,
            )

        if self.is_simple_taxpayer_invoice_question(question):
            expanded_keywords = self.prepend_unique(
                self.SIMPLE_TAXPAYER_INVOICE_QUERY_HINTS,
                expanded_keywords,
            )

        if self.is_taxpayer_type_conversion_question(question):
            expanded_keywords = self.prepend_unique(
                ["간이과세자", "일반과세자", "전환", "직전 연도 공급대가", "제61조", "제109조"],
                expanded_keywords,
            )

        if self.is_recognized_interest_question(question):
            expanded_keywords = self.prepend_unique(
                self.RECOGNIZED_INTEREST_QUERY_HINTS,
                expanded_keywords,
            )

        if self.is_special_relation_fee_question(question, expanded_keywords):
            expanded_keywords = self.prepend_unique(
                self.SPECIAL_RELATION_FEE_QUERY_HINTS,
                expanded_keywords,
            )

        if self.is_income_splitting_question(question):
            expanded_keywords = self.prepend_unique(
                self.INCOME_SPLITTING_QUERY_HINTS,
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

        if self.is_petroleum_tax_question(question, expanded_keywords):
            expanded_keywords = self.prepend_unique(
                self.PETROLEUM_TAX_QUERY_HINTS,
                expanded_keywords,
            )

        if self.is_pre_registration_input_tax_question(question, expanded_keywords):
            expanded_keywords = self.prepend_unique(
                self.PRE_REGISTRATION_INPUT_TAX_QUERY_HINTS,
                expanded_keywords,
            )

        concept_keywords = [
            term
            for term, weight in sorted(
                keyword_weights.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            if weight >= 4.0 or len(concepts) >= 2
        ]
        keywords = self.deduplicate_keep_order(
            concept_keywords + expanded_keywords
        )[:24]

        tax_domain = self.infer_tax_domain(keywords)

        # 개인 프리랜서의 법인 설립을 통한 소득 분산은 질문에 '법인'이
        # 포함되어도 출발점이 개인의 사업소득이므로 소득세 규정을 우선한다.
        if self.is_income_splitting_question(question):
            tax_domain = "소득세"

        law_names = self.extract_law_names(question)
        if not law_names:
            law_names = self.infer_law_names(tax_domain)
        if self.is_income_splitting_question(question):
            law_names = self.prepend_unique(
                ["소득세법", "소득세법 시행령"],
                law_names,
            )
        concept_law_hints = self.deduplicate_keep_order([
            law_name
            for concept in concepts
            for law_name in concept.get("law_hints", [])
        ])
        if concept_law_hints:
            law_names = self.prepend_unique(concept_law_hints, law_names)

        article_numbers = self.extract_article_numbers(question)
        if self.is_closing_inventory_question(question, keywords):
            article_numbers = self.prepend_unique(["제10조"], article_numbers)
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
            "concepts": concepts,
            "keyword_weights": keyword_weights,
            "required_roles": required_roles,
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

        query_embedding = None
        if self.embedding_service is not None:
            query_embedding = await self.embedding_service.embed_query(
                conditions["rewritten_query"] or conditions["original_question"]
            )
            print(
                f"[VECTOR_SEARCH] target=law enabled={query_embedding is not None} "
                f"embedding_dim={len(query_embedding) if query_embedding else 0}",
                flush=True,
            )

        supplemental_conditions = self.build_supplemental_conditions(conditions)
        search_requests = [
            self.client.fetch_chunks(
                candidate_page=1,
                candidate_size=self.candidate_size,
                law_names=conditions["law_names"],
                keywords=conditions["keywords"],
                rewritten_query=conditions["rewritten_query"],
                query_embedding=query_embedding,
            ),
            *(
                self.client.fetch_chunks(
                    candidate_page=1,
                    candidate_size=self.candidate_size,
                    law_names=item["law_names"],
                    keywords=item["keywords"],
                    rewritten_query=item["rewritten_query"],
                    query_embedding=query_embedding,
                )
                for item in supplemental_conditions
            ),
        ]
        search_results = await asyncio.gather(*search_requests)
        raw_chunks, page_info = search_results[0]

        chunks = [self._to_chunk(item) for item in raw_chunks]

        for supplemental_raw_chunks, _ in search_results[1:]:
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
            concepts=conditions.get("concepts", []),
            required_roles=conditions.get("required_roles", []),
        )

        coverage = self.evaluate_concept_coverage(
            ranked,
            conditions.get("concepts", []),
            conditions.get("required_roles", []),
        )
        print(
            f"[SEARCH_COVERAGE] ratio={coverage['ratio']:.3f} "
            f"matched_roles={coverage['matched_roles']} "
            f"missing_roles={coverage['missing_roles']}",
            flush=True,
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
    def build_prompt_context(
        chunks: list[LawChunk],
        max_content_chars: int = 1200,
        keywords: list[str] | None = None,
        keyword_weights: dict[str, float] | None = None,
    ) -> str:
        if not chunks:
            return "검색된 법령 근거가 없습니다."

        contexts: list[str] = []

        for index, chunk in enumerate(chunks, start=1):
            law_name = chunk.law_name or "법령명 없음"
            title = chunk.title or chunk.article or "제목 없음"
            content = ChunkSearchService.extract_relevant_excerpt(
                chunk.content or "",
                max_content_chars,
                keywords or [],
                keyword_weights,
            )

            contexts.append(
                f"[{index}]\n"
                f"법령명: {law_name}\n"
                f"조문명: {title}\n"
                f"내용:\n{content}"
            )

        return "\n\n".join(contexts)

    @staticmethod
    def extract_relevant_excerpt(
        text: str,
        max_chars: int,
        keywords: list[str],
        keyword_weights: dict[str, float] | None = None,
    ) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        # 수수료 지급자는 특수관계인에게 용역을 제공하는 쪽이 아니라
        # 용역을 제공받는 쪽이다. 제98조 전체를 그대로 주면 소형 모델이
        # 제2호(무상·저가 제공)와 제3호(고가 제공받음)를 뒤집기 쉬우므로
        # 공통요건과 거래 방향이 맞는 각 호만 원문 그대로 발췌한다.
        if (
            "특수관계인으로부터" in keywords
            and "제98조(부당행위계산의 부인)" in normalized
            and "3. 특수관계인으로부터" in normalized
        ):
            first_item = normalized.find("1.")
            third_item = normalized.find("3. 특수관계인으로부터")
            fourth_item = normalized.find("4.", third_item)
            fifth_item = normalized.find("5.", fourth_item)
            third_end = fourth_item if fourth_item > third_item else len(normalized)
            fifth_end = normalized.find("③", fifth_item)
            if fifth_end < 0:
                fifth_end = len(normalized)
            relevant_parts = [
                normalized[:first_item].strip() if first_item > 0 else "",
                normalized[third_item:third_end].strip(),
                normalized[fifth_item:fifth_end].strip() if fifth_item > 0 else "",
            ]
            return " ".join(part for part in relevant_parts if part)

        if len(normalized) <= max_chars:
            return normalized

        useful_keywords = [
            keyword.lower()
            for keyword in keywords
            if len(keyword) >= 2 and keyword not in {"개별소비세법", "제1조"}
        ]
        lowered = normalized.lower()

        weights = {
            keyword.lower(): float((keyword_weights or {}).get(keyword, 1.0))
            for keyword in useful_keywords
        }
        anchors: list[int] = []
        for term in useful_keywords:
            search_from = 0
            while len(anchors) < 300:
                position = lowered.find(term, search_from)
                if position < 0:
                    break
                anchors.append(position)
                search_from = position + max(len(term), 1)

        best_start = 0
        best_score = -1.0
        for anchor in anchors or [0]:
            candidate_start = max(0, anchor - max_chars // 3)
            candidate_end = min(len(normalized), candidate_start + max_chars)
            candidate_start = max(0, candidate_end - max_chars)
            window = lowered[candidate_start:candidate_end]
            score = sum(
                weight
                for term, weight in weights.items()
                if term in window
            )
            if score > best_score:
                best_score = score
                best_start = candidate_start

        start = best_start
        end = min(len(normalized), start + max_chars)
        excerpt = normalized[start:end].strip()

        if start > 0:
            excerpt = "..." + excerpt
        if end < len(normalized):
            excerpt += "..."
        return excerpt

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

        if cls.is_recognized_interest_question(question):
            return False
        return any(term in search_text for term in cls.BAD_DEBT_ALLOWANCE_TERMS)

    @classmethod
    def is_exempt_invoice_penalty_question(cls, question: str) -> bool:
        compact_text = re.sub(r"\s+", "", question)
        return (
            "면세사업자" in compact_text
            and "계산서" in compact_text
            and any(term in compact_text for term in ("미발급", "발급하지", "가산세"))
        )

    @classmethod
    def is_simple_taxpayer_invoice_question(cls, question: str) -> bool:
        compact_text = re.sub(r"\s+", "", question)
        return (
            "간이과세" in compact_text
            and "세금계산서" in compact_text
            and any(term in compact_text for term in ("발급", "의무", "경우"))
        )

    @classmethod
    def is_taxpayer_type_conversion_question(cls, question: str) -> bool:
        compact_text = re.sub(r"\s+", "", question)
        return "간이과세자" in compact_text and "일반과세자" in compact_text and any(
            term in compact_text for term in ("전환", "변경", "기준", "판단")
        )

    @classmethod
    def is_recognized_interest_question(cls, question: str) -> bool:
        compact_text = re.sub(r"\s+", "", question)
        return "가지급금" in compact_text and any(
            term in compact_text for term in ("인정이자", "이자계산", "인정이자율")
        )

    @classmethod
    def is_special_relation_fee_question(
        cls,
        question: str,
        keywords: list[str] | None = None,
    ) -> bool:
        search_text = " ".join([question, *(keywords or [])])
        compact_text = re.sub(r"\s+", "", search_text)
        has_special_relation = any(
            term in compact_text
            for term in ("특수관계인", "특수관계자", "계열회사")
        )
        has_fee_or_service = any(
            term in compact_text
            for term in ("수수료", "용역비", "용역", "대가")
        )
        has_denial_issue = any(
            term in compact_text
            for term in (
                "부당행위계산",
                "필요경비",
                "손금",
                "부인",
                "불산입",
            )
        )
        return has_special_relation and has_fee_or_service and has_denial_issue

    @classmethod
    def is_income_splitting_question(cls, question: str) -> bool:
        compact_text = re.sub(r"\s+", "", question)
        has_individual_business = any(
            term in compact_text
            for term in ("프리랜서", "개인사업자", "사업소득")
        )
        has_company = "법인" in compact_text and any(
            term in compact_text for term in ("설립", "전환", "명의")
        )
        has_splitting_issue = any(
            term in compact_text
            for term in ("소득분산", "소득을분산", "소득귀속", "부당행위계산", "실질과세")
        )
        return has_individual_business and has_company and has_splitting_issue

    @classmethod
    def uses_corporate_tax_for_special_relation(cls, question: str) -> bool:
        """법인이라는 단어가 아닌 납세주체와 세목 표현으로 적용 세목을 판별한다."""
        if cls.is_income_splitting_question(question):
            return False
        compact_text = re.sub(r"\s+", "", question)
        return any(term in compact_text for term in ("법인세", "손금", "손금불산입"))

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

    @classmethod
    def is_petroleum_tax_question(
        cls,
        question: str,
        keywords: list[str] | None = None,
    ) -> bool:
        search_text = " ".join([question, *(keywords or [])]).lower()
        compact_text = search_text.replace(" ", "")
        has_excise_tax = any(
            term in compact_text
            for term in ("개별소비세", "개소세", "유류세")
        )
        has_petroleum = any(term in search_text for term in cls.PETROLEUM_TAX_TERMS)
        return has_excise_tax and has_petroleum

    @classmethod
    def is_pre_registration_input_tax_question(
        cls,
        question: str,
        keywords: list[str] | None = None,
    ) -> bool:
        search_text = " ".join([question, *(keywords or [])])
        compact_text = search_text.replace(" ", "")
        has_registration = "사업자등록" in compact_text
        has_before = any(term in compact_text for term in ("등록전", "등록이전", "등록전에", "신청하기전"))
        has_input_tax = "매입세액" in compact_text or (
            "부가가치세" in compact_text and "공제" in compact_text
        )
        return has_registration and has_before and has_input_tax

    @classmethod
    def is_closing_inventory_question(
        cls,
        question: str,
        keywords: list[str] | None = None,
    ) -> bool:
        compact_text = re.sub(r"\s+", "", " ".join([question, *(keywords or [])]))
        return "폐업" in compact_text and any(
            term.replace(" ", "") in compact_text
            for term in ("남아있는재화", "잔존재화", "자기생산ㆍ취득재화", "재고")
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

        concepts = conditions.get("concepts", [])
        required_roles = set(conditions.get("required_roles", []))
        strict_concepts = [
            concept
            for concept in concepts
            if concept.get("role") in required_roles
            or concept.get("role") in {"EXCEPTION", "BENEFIT"}
        ]
        strict_keywords = cls.deduplicate_keep_order([
            term
            for concept in strict_concepts
            for term in concept.get("search_terms", [])[:2]
        ])
        if len(strict_keywords) >= 2:
            concept_law_names = cls.deduplicate_keep_order([
                law_name
                for concept in concepts
                for law_name in concept.get("law_hints", [])
            ])
            strict_law_names = concept_law_names or conditions["law_names"]
            supplemental_conditions.append(
                {
                    "law_names": strict_law_names,
                    "keywords": strict_keywords,
                    "rewritten_query": " ".join(
                        cls.deduplicate_keep_order(
                            strict_law_names + strict_keywords
                        )
                    ),
                }
            )

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

        if cls.is_exempt_invoice_penalty_question(conditions["original_question"]):
            law_names = cls.prepend_unique(
                ["소득세법", "법인세법"],
                conditions["law_names"],
            )
            keywords = cls.prepend_unique(
                cls.EXEMPT_INVOICE_PENALTY_QUERY_HINTS,
                conditions["keywords"],
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": keywords,
                    "rewritten_query": (
                        "면세사업자 계산서 발급 미발급 지연발급 계산서합계표 가산세 "
                        "소득세법 제163조 제81조의10 법인세법 제121조 제75조의8 "
                        "공급가액 2퍼센트 1퍼센트 0.5퍼센트"
                    ),
                }
            )

        if cls.is_simple_taxpayer_invoice_question(conditions["original_question"]):
            law_names = cls.prepend_unique(
                ["부가가치세법", "부가가치세법 시행령"],
                conditions["law_names"],
            )
            keywords = cls.prepend_unique(
                cls.SIMPLE_TAXPAYER_INVOICE_QUERY_HINTS,
                conditions["keywords"],
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": keywords,
                    "rewritten_query": (
                        "부가가치세법 제32조 제36조 제36조의2 제61조 제68조의2 "
                        "부가가치세법 시행령 제109조 간이과세자 세금계산서 발급 의무 "
                        "직전 연도 공급대가 4천800만원 1억4백만원 미발급 가산세"
                    ),
                }
            )

        if cls.is_taxpayer_type_conversion_question(conditions["original_question"]):
            law_names = cls.prepend_unique(
                ["부가가치세법", "부가가치세법 시행령"], conditions["law_names"]
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": ["간이과세자", "일반과세자", "전환", "직전 연도 공급대가", "제61조", "제109조"],
                    "rewritten_query": "부가가치세법 제61조 간이과세 적용범위 일반과세자 전환 직전 연도 공급대가 부가가치세법 시행령 제109조",
                }
            )

        if cls.is_recognized_interest_question(conditions["original_question"]):
            law_names = cls.prepend_unique(
                ["법인세법", "법인세법 시행령"],
                conditions["law_names"],
            )
            keywords = cls.prepend_unique(
                cls.RECOGNIZED_INTEREST_QUERY_HINTS,
                conditions["keywords"],
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": keywords,
                    "rewritten_query": (
                        "법인세법 제52조 법인세법 시행령 제88조 제89조 "
                        "대표이사 특수관계인 가지급금 인정이자 금전 대여 시가 "
                        "가중평균차입이자율 당좌대출이자율 가지급금 적수 365"
                    ),
                }
            )

        if cls.is_special_relation_fee_question(
            conditions["original_question"],
            conditions["keywords"],
        ):
            uses_corporate_tax = cls.uses_corporate_tax_for_special_relation(
                conditions["original_question"]
            )
            law_names = (
                ["법인세법", "법인세법 시행령"]
                if uses_corporate_tax
                else ["소득세법", "소득세법 시행령"]
            )
            article_hints = (
                ["제52조", "제88조", "제89조"]
                if uses_corporate_tax
                else ["제41조", "제98조"]
            )
            keywords = cls.prepend_unique(
                article_hints + cls.SPECIAL_RELATION_FEE_QUERY_HINTS,
                conditions["keywords"],
            )
            supplemental_conditions.append(
                {
                    "law_names": cls.prepend_unique(
                        law_names,
                        conditions["law_names"],
                    ),
                    "keywords": keywords,
                    "rewritten_query": " ".join(
                        cls.deduplicate_keep_order(law_names + keywords)
                    ),
                }
            )

        if cls.is_income_splitting_question(conditions["original_question"]):
            law_names = ["소득세법", "소득세법 시행령"]
            keywords = cls.prepend_unique(
                cls.INCOME_SPLITTING_QUERY_HINTS,
                conditions["keywords"],
            )
            supplemental_conditions.append(
                {
                    "law_names": cls.prepend_unique(law_names, conditions["law_names"]),
                    "keywords": keywords,
                    "rewritten_query": " ".join(
                        cls.deduplicate_keep_order(law_names + keywords)
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

        if cls.is_petroleum_tax_question(
            conditions["original_question"],
            conditions["keywords"],
        ):
            law_names = cls.prepend_unique(
                ["개별소비세법", "개별소비세법 시행령"],
                conditions["law_names"],
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": cls.PETROLEUM_TAX_QUERY_HINTS,
                    "rewritten_query": (
                        "개별소비세법 제1조 과세대상과 세율 석유류 수량 "
                        "리터당 휘발유 경유 등유 중유 프로판 부탄"
                    ),
                }
            )

        if cls.is_pre_registration_input_tax_question(
            conditions["original_question"],
            conditions["keywords"],
        ):
            law_names = cls.prepend_unique(
                ["부가가치세법", "부가가치세법 시행령"],
                conditions["law_names"],
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": cls.PRE_REGISTRATION_INPUT_TAX_QUERY_HINTS,
                    "rewritten_query": (
                        "부가가치세법 제39조 제1항제8호 "
                        "사업자등록을 신청하기 전 매입세액 "
                        "공급시기 과세기간 20일 이내 등록신청일"
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
            "같은",
            "받은", "받다", "다른", "용도", "사용", "정당한", "정당",
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

        return (tax_keywords + general_keywords)[:16]

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
        concepts: list[dict] | None = None,
        required_roles: list[str] | None = None,
    ) -> list[LawChunk]:
        concepts = concepts or []
        required_role_set = set(required_roles or [])
        preferred_law_names = {name.lower() for name in law_names}

        raw_tokens = ChunkSearchService.extract_keywords(query_text)

        query_terms: list[str] = []
        seen: set[str] = set()


        for token in raw_tokens + keywords:
            normalized = (
                " ".join(token.split())
                if " " in token
                else ChunkSearchService.normalize_search_token(token)
            )

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
        normalized_query = re.sub(r"\s+", "", query_text)
        has_supply_value_calculation = any(
            term in normalized_query for term in ("공급가액", "부가가치세포함", "임대료", "월세", "부동산임대", "받은금액", "받은대가", "공급대가")
        )
        has_vehicle_input_tax_question = any(
            term in normalized_query
            for term in ("리스", "사업용차", "사업용자동차", "차량", "승용차", "화물차")
        )
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
        has_recognized_interest = ChunkSearchService.is_recognized_interest_question(
            query_text
        )
        has_simple_taxpayer_invoice = ChunkSearchService.is_simple_taxpayer_invoice_question(
            query_text
        )
        has_taxpayer_type_conversion = ChunkSearchService.is_taxpayer_type_conversion_question(
            query_text
        )
        has_exempt_invoice_penalty = ChunkSearchService.is_exempt_invoice_penalty_question(
            query_text
        )
        has_special_relation_fee = (
            ChunkSearchService.is_special_relation_fee_question(query_text, keywords)
            or ChunkSearchService.is_income_splitting_question(query_text)
        )
        has_business_withholding = ChunkSearchService.is_business_withholding_question(
            query_text,
            keywords,
        )
        has_overseas_stock = ChunkSearchService.is_overseas_stock_question(
            query_text,
            keywords,
        )
        has_petroleum_tax = ChunkSearchService.is_petroleum_tax_question(
            query_text,
            keywords,
        )
        has_pre_registration_input_tax = ChunkSearchService.is_pre_registration_input_tax_question(
            query_text,
            keywords,
        )
        has_closing_inventory = ChunkSearchService.is_closing_inventory_question(
            query_text,
            keywords,
        )

        scored: list[tuple[float, LawChunk]] = []

        for chunk in chunks:
            title_text = (chunk.title or "").lower()
            content_text = (chunk.content or "").lower()
            chunk_type = (chunk.chunk_type or "").upper()

            score = 0.0

            if has_petroleum_tax:
                combined_text = f"{title_text} {content_text}"
                fuel_matches = sum(
                    1
                    for term in ("휘발유", "경유", "등유", "중유", "프로판", "부탄")
                    if term in combined_text
                )
                has_rate_marker = any(
                    marker in combined_text
                    for marker in ("세율", "리터당", "킬로그램당", "과세대상")
                )

                # `경유`는 `~를 경유하여`로도 많이 쓰인다. 다른 유종과
                # 세율 표시가 함께 없으면 석유류 세율 근거로 보지 않는다.
                if fuel_matches < 2 or not has_rate_marker:
                    chunk.score = 0.0
                    continue

                if "개별소비세법" in (chunk.law_name or ""):
                    score += 45.0
                if "제1조" in title_text or "과세대상과 세율" in title_text:
                    score += 80.0
                score += fuel_matches * 12.0
                if "담배" in title_text:
                    score -= 100.0

            if has_pre_registration_input_tax:
                combined_text = f"{title_text} {content_text}"
                has_registration = "사업자등록" in combined_text
                has_input_tax = "매입세액" in combined_text

                if not (has_registration and has_input_tax):
                    chunk.score = 0.0
                    continue
                if "제39조" in title_text:
                    score += 100.0
                if "사업자등록을 신청하기 전" in combined_text:
                    score += 80.0
                if "20일 이내" in combined_text:
                    score += 40.0
                if any(term in title_text for term in ("전환", "재고품", "감가상각자산")):
                    score -= 100.0

            if has_closing_inventory:
                combined_text = f"{title_text} {content_text}"
                # 폐업 잔존재화는 부가가치세법 제10조 제6항이 직접 근거다.
                if "부가가치세법" not in (chunk.law_name or ""):
                    chunk.score = 0.0
                    continue
                if "제10조" in title_text and "폐업" in combined_text and "남아" in combined_text:
                    score += 180.0
                elif "폐업" not in combined_text or "재화" not in combined_text:
                    score -= 60.0

            for term in query_terms:
                title_hits = min(title_text.count(term), 2)
                content_hits = min(content_text.count(term), 3)

                score += (title_hits * 3.0) + content_hits

            combined_text = f"{title_text} {content_text}"
            matched_roles: set[str] = set()
            matched_positions: list[int] = []

            for concept in concepts:
                concept_terms = concept.get("search_terms", [])
                matched_term = next(
                    (
                        term.lower()
                        for term in concept_terms
                        if term and term.lower() in combined_text
                    ),
                    None,
                )
                if matched_term is None:
                    continue

                role = str(concept.get("role") or "")
                if role:
                    matched_roles.add(role)
                concept_weight = float(concept.get("weight") or 0.0)
                matched_in_title = matched_term in title_text
                title_bonus = 2.0 if matched_in_title else 1.0
                score += concept_weight * title_bonus
                if matched_in_title and role == "LEGAL_EFFECT":
                    score += 18.0
                matched_positions.append(combined_text.find(matched_term))

            matched_required_roles = required_role_set & matched_roles
            missing_required_roles = required_role_set - matched_roles
            score += len(matched_required_roles) * 8.0
            score -= len(missing_required_roles) * 14.0

            if required_role_set and not missing_required_roles:
                score += 24.0

            if "LEGAL_EFFECT" in required_role_set and "LEGAL_EFFECT" not in matched_roles:
                score -= 18.0
            if "BREACH" in required_role_set and "BREACH" not in matched_roles:
                score -= 15.0

            valid_positions = [position for position in matched_positions if position >= 0]
            if len(valid_positions) >= 3 and max(valid_positions) - min(valid_positions) <= 1000:
                score += 15.0

            if (chunk.law_name or "").lower() in preferred_law_names:
                score += 6.0

            if has_three_percent or has_business_income or has_withholding:
                if "이자소득" in title_text or "배당소득" in title_text:
                    score -= 8.0

                if "사업소득" in title_text or "사업소득" in content_text:
                    score += 8.0

                if "원천징수" in title_text or "원천징수" in content_text:
                    score += 8.0

                if "인적용역" in title_text or "인적용역" in content_text:
                    score += 5.0

            if has_exempt_invoice_penalty:
                combined_text = f"{title_text} {content_text}"
                target_articles = ("제163조", "제81조의10", "제121조", "제75조의8")
                if any(term in combined_text for term in target_articles):
                    score += 150.0
                if "계산서" in combined_text and "공급가액의 100분의 2" in combined_text:
                    score += 90.0
                if "지연" in combined_text or "다음 달 25일" in combined_text or "1월 25일" in combined_text:
                    score += 45.0
                if "소규모사업자" in combined_text:
                    score += 25.0
                if "계산서의 작성" in title_text and "발급" in title_text:
                    score += 120.0
                if "매입처별 세금계산서합계표" in title_text:
                    score -= 100.0
                if "신용카드" in title_text or "현금영수증" in title_text:
                    score -= 130.0

            if has_simple_taxpayer_invoice:
                combined_text = f"{title_text} {content_text}"
                target_titles = ("제32조", "제36조", "제61조", "제68조", "제109조")
                if any(term in title_text for term in target_titles):
                    score += 130.0
                if "4천800만원" in combined_text:
                    score += 80.0
                if "1억4백만원" in combined_text:
                    score += 55.0
                if "세금계산서" in combined_text and "간이과세자" in combined_text:
                    score += 60.0
                if "수정세금계산서" in title_text or "제70조" in title_text:
                    score -= 150.0

            if has_taxpayer_type_conversion:
                combined_text = f"{title_text} {content_text}"
                if any(term in title_text for term in ("제61조", "제109조")):
                    score += 220.0
                if "일반과세자" in combined_text and "간이과세자" in combined_text:
                    score += 100.0
                if "직전 연도" in combined_text and "공급대가" in combined_text:
                    score += 80.0
                if any(term in title_text for term in ("제44조", "제86조", "재고")):
                    score -= 140.0

            if has_supply_value_calculation:
                combined_text = f"{title_text} {content_text}"
                if "부가가치세법" in (chunk.law_name or "") and "제29조" in title_text:
                    score += 240.0
                if any(term in combined_text for term in ("110분의 100", "110분의 10", "공급가액", "받은 대가", "공급대가")):
                    score += 90.0
                if "부가가치세 포함" in combined_text and "불분명" in combined_text:
                    score += 80.0
                if "부동산임대공급가액명세서" in combined_text or "임대공급가액명세서" in combined_text:
                    score -= 110.0
                if any(term in title_text for term in ("서식", "제출", "신고서")):
                    score -= 70.0

            if has_vehicle_input_tax_question:
                combined_text = f"{title_text} {content_text}"
                if "제39조" in title_text and "자동차" in combined_text:
                    score += 220.0
                if "제78조" in title_text or "운수업" in combined_text or "자동차판매업" in combined_text:
                    score += 120.0
                if any(term in combined_text for term in ("자동차의 구입과 임차", "비영업용 소형승용차", "경형승용자동차")):
                    score += 100.0
                if any(term in title_text for term in ("서식", "명세서", "제출")):
                    score -= 100.0

            if has_recognized_interest:
                combined_text = f"{title_text} {content_text}"
                is_target_article = (
                    ("법인세법" == (chunk.law_name or "") and "제52조" in title_text)
                    or (
                        "법인세법 시행령" in (chunk.law_name or "")
                        and any(term in title_text for term in ("제88조", "제89조"))
                    )
                )
                if is_target_article:
                    score += 140.0
                if "가중평균차입이자율" in combined_text:
                    score += 70.0
                if "당좌대출이자율" in combined_text:
                    score += 70.0
                if "금전" in combined_text and "시가" in combined_text:
                    score += 35.0
                if any(term in title_text for term in ("제19조의2", "제34조", "제61조")):
                    score -= 120.0

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

            if has_special_relation_fee:
                combined_text = f"{title_text} {content_text}"
                uses_corporate_tax = ChunkSearchService.uses_corporate_tax_for_special_relation(
                    query_text
                )
                if uses_corporate_tax:
                    is_target_article = (
                        "법인세법 시행령" in (chunk.law_name or "")
                        and any(term in title_text for term in ("제88조", "제89조"))
                    )
                else:
                    is_target_article = (
                        "소득세법 시행령" in (chunk.law_name or "")
                        and "제98조" in title_text
                        and "부당행위계산" in title_text
                    )

                if is_target_article:
                    score += 120.0
                if "부당행위계산" in combined_text:
                    score += 35.0
                if "특수관계인" in combined_text or "특수관계자" in combined_text:
                    score += 25.0
                if "용역" in combined_text:
                    score += 20.0
                if "시가" in combined_text:
                    score += 15.0

                # 양도자산 취득가액이나 일반 필요경비 조문은 보조 근거일
                # 뿐, 부당행위계산 부인의 직접 기준 조문보다 앞설 수 없다.
                if "제163조" in title_text or "제55조" in title_text:
                    score -= 45.0

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
    def evaluate_concept_coverage(
        chunks: list[LawChunk],
        concepts: list[dict],
        required_roles: list[str],
    ) -> dict:
        required_role_set = set(required_roles)
        if not required_role_set:
            return {
                "ratio": 1.0,
                "matched_roles": [],
                "missing_roles": [],
            }

        matched_roles: set[str] = set()
        for chunk in chunks[:3]:
            text = f"{chunk.title or ''} {chunk.content or ''}".lower()
            for concept in concepts:
                role = str(concept.get("role") or "")
                if role not in required_role_set:
                    continue
                if any(
                    term and term.lower() in text
                    for term in concept.get("search_terms", [])
                ):
                    matched_roles.add(role)

        missing_roles = required_role_set - matched_roles
        return {
            "ratio": len(matched_roles) / len(required_role_set),
            "matched_roles": sorted(matched_roles),
            "missing_roles": sorted(missing_roles),
        }

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
