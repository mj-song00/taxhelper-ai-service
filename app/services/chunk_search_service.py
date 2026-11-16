from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path
import re

from app.schemas.retrieval import LawChunk, Pagination
from app.services.chunk_client import ChunkSearchClient
from app.services.legal_concepts import (
    build_weighted_terms,
    extract_legal_concepts,
    infer_required_roles,
)


def _load_law_name_catalog(path: Path) -> tuple[str, ...]:
    """Load and validate the versioned catalog before search is available."""
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
        if type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1:
            raise ValueError("unsupported schema_version")
        if catalog["catalog_version"] != "law_names_catalog_v1":
            raise ValueError("unsupported catalog_version")
        identity = catalog["integrity"]["source_identity"]
        identity_keys = {"engine", "database", "schema", "table", "column"}
        if not isinstance(identity, dict) or set(identity) != identity_keys:
            raise ValueError("invalid source_identity")
        if any(not isinstance(value, str) or not value.strip() for value in identity.values()):
            raise ValueError("invalid source_identity values")
        if identity != {key: catalog["source"][key] for key in identity_keys}:
            raise ValueError("source_identity mismatch")
        if (identity["engine"], identity["schema"], identity["table"], identity["column"]) != (
            "PostgreSQL", "public", "laws", "name"
        ):
            raise ValueError("unexpected catalog source")
        names = catalog["law_names"]
        if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("invalid law_names")
        if any(type(catalog[key]) is not int or catalog[key] != 419 for key in ("source_row_count", "name_count")) or len(names) != 419:
            raise ValueError("invalid v1 catalog count")
        if len(set(names)) != len(names):
            raise ValueError("duplicate law names")
        compact = [re.sub(r"\s+", "", name) for name in names]
        if len(set(compact)) != len(compact):
            raise ValueError("whitespace normalization collision")
        if names != sorted(names, key=lambda name: (-len(re.sub(r"\s+", "", name)), name)):
            raise ValueError("invalid catalog ordering")
        if not isinstance(catalog["ordering"], dict) or not isinstance(catalog["normalization"], dict):
            raise ValueError("invalid catalog metadata")
        if catalog["normalization"].get("original_names_preserved") is not True or catalog["normalization"].get("use_normalized_names_as_canonical") is not False:
            raise ValueError("canonical names must be preserved")
        payload = {key: catalog[key] for key in (
            "schema_version", "catalog_version", "source_row_count", "name_count",
            "ordering", "normalization", "law_names"
        )}
        payload["source_identity"] = identity
        digest = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if catalog["integrity"]["algorithm"] != "SHA-256" or digest != catalog["integrity"]["content_sha256"]:
            raise ValueError("content_sha256 mismatch")
        return tuple(names)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Cannot initialize canonical law catalog: {path} ({type(exc).__name__})") from exc


_CANONICAL_LAW_NAMES = _load_law_name_catalog(
    Path(__file__).resolve().parents[1] / "data" / "law_names_catalog_v1.json"
)
_CANONICAL_LAW_NAME_SET = frozenset(_CANONICAL_LAW_NAMES)
_LAW_NAME_DELIMITER = r"(?=$|[\s.,;:!?()\[\]{}<>「」『』·])"
_LAW_NAME_PARTICLE = r"(?:상(?:의|으로)?|에서(?:는)?|에(?:서는|서|따라|따른)?|으로(?:서|써)?|의|은|는|이|가|을|를|과|와)"
_LAW_NAME_END = re.compile(
    r"(?=$|[\s.,;:!?()\[\]{}<>「」『』·]|" + _LAW_NAME_PARTICLE + _LAW_NAME_DELIMITER + ")"
)
_CANONICAL_LAW_PATTERN = re.compile(
    r"(?<![0-9A-Za-z가-힣])(?:"
    + "|".join(
        "(?P<L" + str(index) + ">"
        + r"[ \t]*".join(re.escape(part) for part in name.split()) + ")"
        for index, name in enumerate(_CANONICAL_LAW_NAMES)
    )
    + ")" + _LAW_NAME_END.pattern
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
            "프리랜서", "사업소득", "근로소득", "종합소득세", "종합소득", "원천징수", "필요경비",
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

    EXPLICIT_DOMAIN_HINTS = {
        "사업소득", "근로소득", "종합소득세", "종합소득", "양도소득세",
        "부가세", "부가가치세",
        "법인", "법인세",
        "개별소비세", "개별 소비세", "개소세",
        "지방세", "취득세", "재산세", "등록면허세",
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
        "대손세액공제": ["부가가치세", "매출세액"],
        "공급가액": ["부가가치세 포함", "110분의 100", "110분의 10", "제29조"],
        "부가가치세 포함": ["공급가액", "110분의 100", "제29조"],
        "임대료": ["공급가액", "부가가치세 포함", "제29조"],
        "월세": ["받은 대가", "공급가액", "공급대가", "110분의 100", "부가가치세 포함", "제29조"],
        "부동산 임대": ["받은 대가", "공급가액", "공급대가", "110분의 100", "부가가치세 포함", "제29조"],
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
        "대손충당금", "대손금", "채권", "구상채권", "가지급금", "채무보증",
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
        "국외 주식", "국외주식",
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

    NOMINEE_BUSINESS_QUERY_HINTS = [
        "국세기본법",
        "제14조",
        "실질과세",
        "명의일 뿐이고",
        "사실상 귀속되는 자",
        "납세의무자",
        "손익의 귀속",
        "지배ㆍ관리",
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
    ) -> None:
        self.client = client
        self.candidate_size = candidate_size

    def build_search_conditions(self, question: str) -> dict:
        keywords = self.extract_keywords(question)
        if self.is_closing_inventory_question(question, keywords):
            keywords = self.prepend_unique(
                ["폐업", "잔존재화", "남아 있는 재화", "자기생산ㆍ취득재화"],
                keywords,
            )
        normalized_question = re.sub(r"\s+", "", question)
        if (
            any(term in normalized_question for term in ("임대", "임대료", "월세", "부동산임대"))
            and any(term in normalized_question for term in ("부가가치세", "부가세", "VAT", "vat", "공급가액", "공급대가", "부가가치세포함", "부가세포함", "별도로받지", "받은대가"))
        ):
            keywords = self.prepend_unique(
                ["공급가액", "받은 대가", "공급대가", "부가가치세 포함 여부 불분명", "110분의 100", "110분의 10", "부가가치세법 제29조"],
                keywords,
            )
        if (
            any(term in normalized_question for term in ("리스", "사업용차", "사업용자동차", "차량", "승용차", "화물차"))
            and any(term in normalized_question for term in ("부가가치세", "매입세액", "매입세액공제", "세금계산서", "공제받", "공제가능", "공제여부"))
        ):
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

        if self.is_nominee_business_question(question):
            expanded_keywords = self.prepend_unique(
                self.NOMINEE_BUSINESS_QUERY_HINTS,
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

        # Scope expansion uses tax names in the original question, not expanded keywords.
        explicit_domain_positions: dict[str, int] = {}
        for domain, domain_hints in self.DOMAIN_HINTS.items():
            explicit_hints = (domain_hints | {domain}) & (self.EXPLICIT_DOMAIN_HINTS | {domain})
            for hint in explicit_hints:
                if not hint.endswith("세"):
                    continue
                pattern = r"[ \t]*".join(re.escape(part) for part in hint.split())
                match = re.search(pattern, question)
                if match is not None:
                    explicit_domain_positions[domain] = min(
                        match.start(), explicit_domain_positions.get(domain, match.start())
                    )
        explicit_tax_domains = sorted(explicit_domain_positions, key=explicit_domain_positions.get)

        extracted_law_names = self.extract_law_names(question)
        canonical_law_names = [name for name in extracted_law_names if name in _CANONICAL_LAW_NAME_SET]
        unresolved_law_names = [name for name in extracted_law_names if name not in _CANONICAL_LAW_NAME_SET]
        # Explicit unresolved names keep the existing restricted search scope.
        if canonical_law_names or unresolved_law_names:
            law_names = extracted_law_names
        elif len(explicit_tax_domains) > 1:
            law_names = []
        else:
            law_names = self.infer_law_names(tax_domain)
        if len(explicit_tax_domains) > 1:
            for domain in explicit_tax_domains:
                domain_laws = self.infer_law_names(domain)
                if domain_laws and not any(name in domain_laws for name in law_names):
                    # Unspecified subordinate legislation is not added for every domain.
                    law_names = self.deduplicate_keep_order(law_names + [domain_laws[0]])
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
        if self.is_nominee_business_question(question):
            law_names = self.prepend_unique(["국세기본법"], law_names)

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

        conditions = {
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

        if len(explicit_tax_domains) > 1:
            conditions["explicit_tax_domains"] = explicit_tax_domains
            conditions["catalog_missing_law_names"] = [
                name for name in law_names if name not in _CANONICAL_LAW_NAME_SET
            ]
        return conditions

    async def retrieve_chunks(
        self,
        conditions: dict,
        top_k: int,
    ) -> tuple[list[LawChunk], Pagination]:
        top_k = max(1, min(int(top_k), 20))

        print("keywords =", conditions["keywords"])
        print("query =", conditions["rewritten_query"])
        print("law_names =", conditions["law_names"])

        supplemental_conditions = self.build_supplemental_conditions(conditions)
        search_requests = [
            self.client.fetch_chunks(
                candidate_page=1,
                candidate_size=self.candidate_size,
                law_names=conditions["law_names"],
                keywords=conditions["keywords"],
                rewritten_query=conditions["rewritten_query"],
            ),
            *(
                self.client.fetch_chunks(
                    candidate_page=1,
                    candidate_size=self.candidate_size,
                    law_names=item["law_names"],
                    keywords=item["keywords"],
                    rewritten_query=item["rewritten_query"],
                )
                for item in supplemental_conditions
            ),
        ]
        initial_results = await asyncio.gather(*search_requests, return_exceptions=True)
        if isinstance(initial_results[0], Exception):
            raise initial_results[0]
        search_results = [result for result in initial_results if not isinstance(result, Exception)]
        initial_supplemental_errors = [type(result).__name__ for result in initial_results[1:] if isinstance(result, Exception)]
        if initial_supplemental_errors:
            print("[SEARCH_SUPPLEMENT_ERROR] " + json.dumps(initial_supplemental_errors))
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

        effective_law_names = list(conditions["law_names"])
        scope_diagnostics: list[dict] = []
        scope_seen: set[tuple] = set()
        scope_pending: list[dict] = []
        scope_frontier = chunks
        scope_added_ids: set[str] = set()
        scope_request_count = 0
        scope_budget = max(0, min(60, 120 - len(chunks)))
        for depth in range(1, 3):
            remaining_requests = 4 - scope_request_count
            if remaining_requests <= 0 or len(scope_added_ids) >= scope_budget:
                break
            discovered = self.build_supplemental_conditions(
                {**conditions, "law_names": effective_law_names},
                retrieved_chunks=scope_frontier, visited=scope_seen,
            )
            queued_keys = {tuple(item["request_key"]) for item in scope_pending}
            fresh = []
            for item in discovered:
                key = tuple(item["request_key"])
                if key not in scope_seen and key not in queued_keys:
                    fresh.append({**item, "discovery_round": depth})
                    queued_keys.add(key)
            scope_plans = []
            slots = min(2, remaining_requests)
            # Share a round between waiting work and newly discovered work.
            # Keep each pool's existing relevance order; do not alter rank scores.
            if scope_pending and fresh:
                scope_plans.append(scope_pending.pop(0))
                if slots > 1:
                    scope_plans.append(fresh.pop(0))
            for pool in (scope_pending, fresh):
                while pool and len(scope_plans) < slots:
                    scope_plans.append(pool.pop(0))
            scope_pending.extend(fresh)
            if not scope_plans or len(scope_added_ids) >= scope_budget:
                break
            scope_request_count += len(scope_plans)
            scope_seen.update(tuple(plan["request_key"]) for plan in scope_plans)
            scope_results = await asyncio.gather(*(
                self.client.fetch_chunks(
                    candidate_page=1, candidate_size=self.candidate_size,
                    law_names=plan["law_names"], keywords=plan["keywords"],
                    rewritten_query=plan["rewritten_query"],
                ) for plan in scope_plans
            ), return_exceptions=True)
            scope_frontier = []
            existing_ids = {chunk.chunk_id for chunk in chunks}
            for plan, result in zip(scope_plans, scope_results):
                diagnostic = {key: value for key, value in plan.items() if key != "delegation_basis"}
                diagnostic.update({"depth": depth, "execution_round": depth,
                                   "relation_depth": plan["discovery_round"],
                                   "accepted_ids": [], "returned_count": 0,
                                   "unproven_delegation_ids": []})
                if isinstance(result, Exception):
                    diagnostic["error_type"] = type(result).__name__
                    scope_diagnostics.append(diagnostic)
                    print("[LAW_SCOPE] " + json.dumps(diagnostic, ensure_ascii=False))
                    continue
                raw_scope, _ = result
                diagnostic["returned_count"] = len(raw_scope)
                for raw_item in raw_scope:
                    candidate = self._to_chunk(raw_item)
                    if candidate.law_name not in plan["law_names"]:
                        continue
                    header = re.match(r"^\s*(제\s*\d+\s*조(?:\s*의\s*\d+)?)\s*[(（]", candidate.content or "")
                    if header is None:
                        continue
                    article = re.sub(r"\s+", "", header.group(1))
                    if plan["kind"] == "reference":
                        valid = article == plan["target_article"]
                    else:
                        # Verify a parent-side mandate and a child-side provision,
                        # not merely the presence of the parent's article number.
                        parent_name = re.escape(re.sub(r"\s+", "", plan["from_law"]))
                        parent_prefix = r"(?:" + plan["parent_alias"] + r"|「" + parent_name + r"」|" + parent_name + r")"
                        citation_suffix = r"(?:(?:제\d+(?:항|호)|[가-하]목|각호|외의부분|본문|단서|부터|까지|및|또는))*"
                        lead = r"^(?:제\d+항(?:에도불구하고|을적용할때|에따라),?)?"
                        backref = re.compile(lead + parent_prefix + re.escape(plan["parent_article"]) + r"(?![0-9]|의\d)")
                        valid = False
                        units = re.split(r"(?=[①-⑳])|\n(?=\s*\d+(?:의\d+)?\.\s)", candidate.content or "")
                        for unit in units:
                            unit = re.sub(r"^\s*제\s*\d+\s*조(?:\s*의\s*\d+)?\s*[(（][^\n)）]*[)）]", "", unit)
                            unit = re.sub(r"^\s*(?:[①-⑳]|\d+(?:의\d+)?\.)\s*", "", unit)
                            compact = re.sub(r"\s+", "", unit)
                            reference = backref.match(compact)
                            if reference is None:
                                continue
                            tail = compact[reference.end():]
                            definition = re.match(citation_suffix + r'에서(?:["“]([^"”]+)["”]|((?:대통령령|[가-힣]+부령|총리령)으로정하는[^.。]*))', tail)
                            implementation = re.match(citation_suffix + r"(?:(?:을|를)적용할때|(?:을|를)적용함에있어|의규정에의한|의규정에따른|에따른)", tail)
                            for basis in plan.get("delegation_basis", []):
                                definition_text = definition.group(0) if definition else ""
                                instrument = r"대통령령" if basis["instrument"] == "decree" else r"(?:[가-힣]+부령|총리령)"
                                definition_link = bool(definition and re.search(instrument + r"(?:으로|에서)정", definition_text))
                                subject_link = any(term in compact for term in basis["subject_terms"])
                                if subject_link and (definition_link or implementation):
                                    valid = True
                                    diagnostic.setdefault("relation_evidence", []).append({
                                        "chunk_id": candidate.chunk_id, "parent_unit": basis["unit"],
                                        "link": "definition" if definition_link else "implementation",
                                    })
                                    break
                            if valid:
                                break
                        if not valid:
                            diagnostic["unproven_delegation_ids"].append(candidate.chunk_id)
                    if not valid or candidate.chunk_id in existing_ids or len(scope_added_ids) >= scope_budget:
                        continue
                    existing_ids.add(candidate.chunk_id)
                    scope_added_ids.add(candidate.chunk_id)
                    scope_frontier.append(candidate)
                    diagnostic["accepted_ids"].append(candidate.chunk_id)
                    effective_law_names = self.deduplicate_keep_order(effective_law_names + [candidate.law_name])
                scope_diagnostics.append(diagnostic)
                print("[LAW_SCOPE] " + json.dumps(diagnostic, ensure_ascii=False))
            if scope_frontier:
                chunks = self.deduplicate_chunks(chunks + scope_frontier)
                chunks = self.prioritize_by_law_names(chunks, effective_law_names)
            elif not scope_pending:
                break

        scope_deferred_conditions: list[dict] = []
        stop_reason = ("candidate_budget_exhausted" if len(scope_added_ids) >= scope_budget else
                       "request_budget_exhausted" if scope_request_count >= 4 else "round_limit_reached")
        for plan in scope_pending:
            diagnostic = {key: value for key, value in plan.items() if key != "delegation_basis"}
            diagnostic.update({"status": "not_executed", "reason": stop_reason})
            scope_deferred_conditions.append(diagnostic)
            print("[LAW_SCOPE_DEFERRED] " + json.dumps(diagnostic, ensure_ascii=False))

        ranked = self.rank_chunks(
            chunks=chunks,
            keywords=conditions["keywords"],
            law_names=conditions["law_names"],
            query_text=conditions["original_question"],
            top_k=top_k,
            concepts=conditions.get("concepts", []),
            required_roles=conditions.get("required_roles", []),
            explicit_tax_domains=conditions.get("explicit_tax_domains", []),
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
        *,
        return_details: bool = False,
        question: str = "",
        search_conditions: dict | None = None,
        max_context_chars: int | None = None,
        context_prefix: str = "[법령 근거]\n",
        reserved_context: str = "",
        context_suffix: str = "",
        context_policy: str | None = None,
    ) -> str | dict:
        if context_policy == "joint-optional-budget-v1":
            # 상세 진단 모드와 분리된, 검증된 D 입력 구성 정책이다.
            from app.core.config import get_settings

            configured_limit = get_settings().ollama_max_context_chars
            limit = min(3000, configured_limit, configured_limit if max_context_chars is None else max_context_chars)
            if limit < 0:
                raise ValueError("문자 예산은 음수일 수 없습니다.")
            conditions = dict(search_conditions or {})
            question = question or conditions.get("original_question", "")
            if keywords is not None:
                conditions["keywords"] = keywords
            if keyword_weights is not None:
                conditions["keyword_weights"] = keyword_weights

            # 실험의 원문 좌표·도입부·단서/참조 추적을 그대로 옮긴 지역 연산이다.
            def norm(text):
                return re.sub(r'\s+', ' ', text).strip()

            def trimmed(text, span):
                a, b = span
                raw = text[a:b]
                return [a + len(raw) - len(raw.lstrip()), b - len(raw) + len(raw.rstrip())]

            def merged(spans):
                result = []
                for a, b in sorted(spans):
                    if a >= b:
                        continue
                    if result and a <= result[-1][1]:
                        result[-1][1] = max(result[-1][1], b)
                    else:
                        result.append([a, b])
                return result

            def excerpt(text, spans):
                return ' ... '.join(norm(text[a:b]) for a, b in spans)

            def connection_metadata(detail, spans):
                result = {}
                for key in ('qualifications', 'references'):
                    expressions = []
                    for item in detail[key]:
                        a, b = item['source_range']
                        if any(x < b and a < y for x, y in spans):
                            expressions.append({'expression': item['expression'], 'source_range': [a, b],
                                                'fully_preserved': any(x <= a and b <= y for x, y in spans),
                                                'connection_status': 'unverified', 'target_status': 'unverified'})
                    result[key] = expressions
                result['legal_applicability'] = 'unverified'
                return result

            def candidates_for_source(source, rank, question, conditions):
                """ID·gold를 읽지 않고 기존 구조 분석 결과와 질문 어휘만 사용한다."""
                text = source['content']
                detail = ChunkSearchService.extract_relevant_excerpt(text, len(norm(text)), [], return_details=True)
                # 줄 중간 숫자를 독립 경계로 취급하지 않는다.
                units = [u for u in detail['structure_units']
                         if not text[text.rfind('\n', 0, u['source_range'][0]) + 1:u['source_range'][0]].strip()]
                by_range = {tuple(u['source_range']): u for u in units}
                terms = {t: float(conditions.get('keyword_weights', {}).get(t, 1))
                         for t in conditions.get('keywords', []) if len(t) >= 2}
                for concept in conditions.get('concepts', []):
                    for term in concept.get('search_terms', []):
                        if len(term) >= 2:
                            terms.setdefault(term, 1.0)
                explicit = re.findall(r'제\s*(\d+)\s*(항|호)', question)
                output = []
                for unit in units:
                    children = [u for u in units if u['parent_range'] == unit['source_range']]
                    # 부칙의 항·호 없는 조도 완전 단위 후보로 지원한다. 본문 항이 있는 조 전체는 제외한다.
                    if unit['unit_type'] not in ('paragraph', 'item', 'article'):
                        continue
                    if unit['unit_type'] == 'article' and children:
                        continue
                    spans = [list(unit['source_range'])]
                    parents = []
                    parent = by_range.get(tuple(unit['parent_range'])) if unit['parent_range'] else None
                    while parent is not None:
                        parents.append(parent)
                        siblings = [u for u in units if u['parent_range'] == parent['source_range']]
                        end = min(u['source_range'][0] for u in siblings)
                        spans.append([parent['source_range'][0], end])
                        parent = by_range.get(tuple(parent['parent_range'])) if parent['parent_range'] else None
                    # 부칙 식별자는 조문 구조와 별도로 정확한 원문 범위를 보존한다.
                    preamble = re.match(r'\s*부칙\s*<[^>]+>[^\n]*', text)
                    if preamble:
                        spans.append([preamble.start(), preamble.end()])
                    spans = merged([trimmed(text, x) for x in spans])
                    descendants = [u for u in detail['structure_units']
                                   if unit['source_range'][0] <= u['source_range'][0] < unit['source_range'][1]]
                    structure_ok = all(u['identification_status'] == 'verified' for u in [unit, *parents, *descendants])
                    matches = [{'term': t, 'weight': w} for t, w in terms.items()
                               if t.lower() in text[slice(*unit['source_range'])].lower()]
                    weight = sum(m['weight'] for m in matches)
                    explicit_match = any((kind == '항' and unit['unit_type'] == 'paragraph'
                                          and unit['unit_label'] == chr(ord('①') + int(number) - 1))
                                         or (kind == '호' and unit['unit_type'] == 'item'
                                             and unit['unit_label'] == number + '.') for number, kind in explicit)
                    body = excerpt(text, spans)
                    block = {'chunk_id': str(source['chunk_id']), 'search_rank': rank, 'law_name': source.get('law_name') or '법령명 없음',
                             'title': source.get('title') or source.get('article') or '제목 없음',
                             'unit_type': unit['unit_type'], 'unit_label': unit['unit_label'],
                             'unit_range': list(unit['source_range']), 'ranges': spans, 'excerpt': body,
                             'position_status': 'verified',
                             'structure_status': 'verified' if structure_ok else 'unverified',
                             'identity_status': 'source_range_preserved' if preamble else 'source_metadata_only',
                             'matches': matches, 'explicit_unit_match': explicit_match,
                             'selection_score': [int(explicit_match), weight / math.sqrt(max(1, len(body))), weight, -len(body)],
                             'connections': connection_metadata(detail, spans),
                             'classification': 'complete_unit' if structure_ok else 'limited_quote',
                             'eligible': bool(weight or explicit_match), 'decision': 'pending',
                             'structure_findings': [{'unit_label': u['unit_label'], 'source_range': list(u['source_range']),
                                                     'status': u['identification_status']}
                                                    for u in [unit, *parents, *descendants] if u['identification_status'] != 'verified']}
                    output.append(block)
                return output, detail

            def render(blocks):
                parts = []
                for index, block in enumerate(blocks, 1):
                    label = ("[완전 단위: 구조 경계 확인·연결/적용 별도]\n"
                             if block["classification"] == "complete_unit" else
                             "[제한적 인용: 완전 단위 아님·구조미확인·연결/적용 미확인]\n")
                    parts.append(f"[{index}]\n법령명: {block['law_name']}\n조문명: {block['title']}\n내용:\n{label}{block['excerpt']}")
                return context_prefix + "\n\n".join(parts) + reserved_context + context_suffix

            if len(render([])) > limit:
                raise ValueError("외곽·예약 근거가 문자 예산을 초과합니다.")
            groups = []
            for rank, chunk in enumerate(chunks, 1):
                candidates, _ = candidates_for_source(chunk.model_dump(), rank, question, conditions)
                groups.append(sorted(candidates, key=lambda block: block["selection_score"], reverse=True))
            selected = []
            events = []
            # B의 첫 pass만 필요하다. 두 번째 pass와 기존 C를 계산하지 않는다.
            for group in groups:
                for candidate in group:
                    if not candidate["eligible"] or candidate["structure_status"] != "verified":
                        continue
                    trial = sorted(selected + [candidate], key=lambda block: (block["search_rank"], block["unit_range"][0]))
                    chars = len(render(trial))
                    events.append({"phase": "initial", "search_rank": candidate["search_rank"],
                                   "unit_range": candidate["unit_range"], "trial_chars": chars,
                                   "accepted": chars <= limit})
                    if chars <= limit:
                        selected = trial
                        break
            initial_keys = {(block["search_rank"], tuple(block["unit_range"])) for block in selected}
            candidates = [candidate for group in groups for candidate in group]
            candidates.sort(key=lambda block: (block["search_rank"], *[-value for value in block["selection_score"]],
                                              block["classification"] != "complete_unit", block["unit_range"][0]))
            for candidate in candidates:
                candidate_key = (candidate["search_rank"], tuple(candidate["unit_range"]))
                if candidate_key in initial_keys:
                    decision = "protected_B_first_pass"
                elif not candidate["eligible"]:
                    decision = "no_question_term_or_explicit_unit_match"
                elif any(block["search_rank"] == candidate["search_rank"] and
                         max(block["unit_range"][0], candidate["unit_range"][0]) <
                         min(block["unit_range"][1], candidate["unit_range"][1]) for block in selected):
                    decision = "selected_unit_overlap"
                else:
                    trial = sorted(selected + [candidate], key=lambda block: (block["search_rank"], block["unit_range"][0]))
                    chars = len(render(trial))
                    events.append({"phase": "joint", "search_rank": candidate["search_rank"],
                                   "unit_range": candidate["unit_range"], "trial_chars": chars,
                                   "accepted": chars <= limit})
                    if chars <= limit:
                        selected = trial
                        decision = "selected_joint_optional"
                    else:
                        decision = "budget_exceeded_no_truncation"
                candidate["D_decision"] = decision
            context = render(selected)
            result = {"input_version": context_policy, "context": context, "char_count": len(context),
                      "char_limit": limit, "blocks": selected, "candidates": candidates, "events": events,
                      "initial_keys": [[rank, list(span)] for rank, span in sorted(initial_keys)],
                      "budget": "within", "legal_applicability": "unverified"}
            return result if return_details else context
        if context_policy is not None:
            raise ValueError("지원하지 않는 입력 구성 정책입니다.")
        if return_details:
            # 상세 모드만 입력 구성 후보를 계산하며 기존 서비스 경로는 유지한다.
            from app.core.config import get_settings

            configured_limit = get_settings().ollama_max_context_chars
            limit = min(3000, configured_limit,
                        configured_limit if max_context_chars is None else max_context_chars)
            if limit < 0:
                raise ValueError("문자 예산은 음수일 수 없습니다.")
            conditions = search_conditions or {}
            question = question or conditions.get("original_question", "")
            terms = keywords if keywords is not None else conditions.get(
                "keywords", ChunkSearchService.extract_keywords(question))
            weights = keyword_weights if keyword_weights is not None else conditions.get("keyword_weights", {})
            concepts = conditions.get("concepts", extract_legal_concepts(question))
            required_roles = set(conditions["required_roles"] if "required_roles" in conditions
                                 else infer_required_roles(concepts))
            explicit_laws = ChunkSearchService.extract_law_names(question)
            explicit_articles = set(ChunkSearchService.extract_article_numbers(question))
            needed_laws = list(explicit_laws)
            if not needed_laws:
                for domain in conditions.get("explicit_tax_domains", []):
                    names = ChunkSearchService.infer_law_names(domain)
                    if names and names[0] not in needed_laws:
                        needed_laws.append(names[0])
            candidates: list[dict] = []
            reasons: list[str] = []
            diagnostics = {"candidates": [], "reservation_events": [], "failures": [],
                           "first_failure": None, "selected_candidates": [],
                           "first_failure_scope": "selected_or_missing_required_candidates",
                           "connection_classification_version": "a3c-v1", "connection_assessments": [],
                           "budget_exceeded": None}
            candidate_diagnostics: dict[int, dict] = {}
            # 명시적인 질문 행위와 제외 문구만 구별하며 법률상 적용을 추론하지 않는다.
            excluded_conditions = []
            question_tail = question
            exclusion_pattern = (
                r"(?P<object>[가-힣A-Za-z0-9ㆍ·]+)(?:을|를)\s*"
                r"(?P<action>신고|제출)(?:하는|한)\s*(?:경우|대상)"
                r"(?:(?:가|이)\s*아닌|(?:을|를)?\s*제외한)"
            )
            for match in re.finditer(exclusion_pattern, question):
                excluded_conditions.append({"object": match.group("object"), "action": match.group("action"),
                                            "question_range": (match.start(), match.end())})
                question_tail = question_tail[:match.start()] + " " * (match.end() - match.start()) + question_tail[match.end():]
            requested_actions = sorted(set(re.findall(
                r"(?<![가-힣])(신고|제출)(?:해야|하여야|하나요|하면|\s*기한)", question_tail)))
            purpose_active = (conditions.get("action", ChunkSearchService.detect_action(question)) == "신고"
                              and bool(excluded_conditions or re.search(r"(?<![가-힣])(?:신고|제출)", question_tail)))
            deadline_requested = bool(re.search(r"기한|언제|까지|며칠", question_tail))
            question_condition_unknown = purpose_active and bool(re.search(r"아닌|제외", question_tail))
            diagnostics["question_purpose"] = {"active": purpose_active, "actions": requested_actions,
                "deadline_requested": deadline_requested, "excluded_conditions": excluded_conditions,
                "condition_unverified": question_condition_unknown}
            for rank, chunk in enumerate(chunks, 1):
                text = chunk.content or ""
                detail = ChunkSearchService.extract_relevant_excerpt(
                    text, len(re.sub(r"\s+", " ", text).strip()), [], return_details=True)
                units = detail["structure_units"]
                by_range = {unit["source_range"]: unit for unit in units}
                articles = {re.sub(r"\s+", "", unit["unit_label"])
                            for unit in units if unit["unit_type"] == "article"
                            and unit["identification_status"] == "verified"}
                # 제목의 가지번호 손실을 본문 조 번호와 혼동하지 않는다.
                article_match = bool(articles & explicit_articles)
                # 구조상 포함과 문구 대응은 법률상 적용·참조 필요성 검증과 분리한다.
                connection_assessments: dict[tuple, dict] = {}
                for field in ("qualifications", "references"):
                    for expression in detail[field]:
                        expression_range = expression["source_range"]
                        expression_start, expression_end = expression_range
                        raw_expression = text[expression_start:expression_end]
                        containers = [unit for unit in units
                                      if unit["identification_status"] == "verified"
                                      and unit["source_range"][0] <= expression_start
                                      and expression_end <= unit["source_range"][1]]
                        owner = min(containers, key=lambda unit: unit["source_range"][1] - unit["source_range"][0]) if containers else None
                        ambiguous = any(unit["identification_status"] != "verified"
                                        and unit["source_range"][0] < expression_end
                                        and expression_start < unit["source_range"][1] for unit in units)
                        structurally_verified = bool(owner is not None and not ambiguous
                            and (field == "references" or expression.get("owner_range") == owner["source_range"]))
                        question_matches = []
                        for condition in re.finditer(
                            r"[가-힣A-Za-z0-9ㆍ·]+(?:을|를)\s*[가-힣]+(?:한|하는)\s*경우", question
                        ):
                            pattern = r"\s*".join(re.escape(part) for part in condition.group().split())
                            for match in re.finditer(pattern, raw_expression):
                                question_matches.append({"kind": "condition_text", "question_range": (condition.start(), condition.end()),
                                    "source_range": (expression_start + match.start(), expression_start + match.end()),
                                    "text": match.group()})
                        if field == "references":
                            pattern = r"\s*".join(re.escape(part) for part in raw_expression.split())
                            if pattern:
                                question_matches.extend({"kind": "reference_text", "question_range": (match.start(), match.end()),
                                                         "source_range": expression_range, "text": raw_expression}
                                                        for match in re.finditer(pattern, question))
                        lexical_matches = []
                        for term in sorted(set(terms)):
                            if len(term) < 2 or re.search(re.escape(term), question, re.IGNORECASE) is None:
                                continue
                            lexical_matches.extend({"term": term,
                                "source_range": (expression_start + match.start(), expression_start + match.end())}
                                for match in re.finditer(re.escape(term), raw_expression, re.IGNORECASE))
                        target = None
                        scope = None
                        if field == "references":
                            target_match = re.search(
                                r"(?:「(?P<law>[^」]+)」\s*)?(?P<article>제\s*\d+\s*조(?:\s*의\s*\d+)?)"
                                r"(?P<units>(?:\s*제\s*\d+\s*(?:항|호|목))*)", raw_expression)
                            if target_match:
                                target = {"law_name": target_match.group("law"),
                                          "article": re.sub(r"\s+", "", target_match.group("article")),
                                          "units": re.findall(r"제\s*\d+\s*(?:항|호|목)", target_match.group("units"))}
                                scope = ("explicit_other_article" if articles and target["article"] not in articles
                                         else "same_article_label" if target["article"] in articles else "unverified")
                                if target["law_name"] is not None and re.sub(r"\s+", "", target["law_name"]) != re.sub(r"\s+", "", chunk.law_name or ""):
                                    scope = "explicit_other_law"
                        classifications = ["structural_connection" if structurally_verified else "connection_uncertain"]
                        if question_matches:
                            classifications.append("question_related")
                        if scope in {"explicit_other_article", "explicit_other_law"}:
                            classifications.append("external_reference")
                        assessment = {"chunk_id": chunk.chunk_id, "search_rank": rank,
                            "expression_type": "qualification" if field == "qualifications" else "reference",
                            "expression": raw_expression, "source_range": expression_range,
                            "preservation_range": (expression_start + len(raw_expression) - len(raw_expression.lstrip()),
                                                   expression_end - len(raw_expression) + len(raw_expression.rstrip())),
                            "owner_range": expression.get("owner_range"),
                            "structural_owner_range": owner["source_range"] if owner is not None else None,
                            "structure_status": "verified" if structurally_verified else "unverified",
                            "target_range": expression.get("target_range"), "classifications": classifications,
                            "question_relation": {"status": "explicit_text_match" if question_matches else "lexical_overlap" if lexical_matches else "unverified",
                                                  "evidence": question_matches, "lexical_matches": lexical_matches},
                            "reference_target": target, "reference_scope": scope,
                            "target_verification": {"existence": "unverified", "content": "unverified", "legal_effect": "unverified"}
                                                   if field == "references" else None,
                            "reference_necessity": "unverified" if field == "references" else None,
                            "legal_applicability": "unverified", "selected_block_preserved": False,
                            "effective_context_preserved": False, "selected_failure": False,
                            "decision": "not_in_selected_blocks",
                            "unresolved_reason": "structural_owner_unverified" if not structurally_verified else
                                "reference_necessity_unverified" if field == "references" else "legal_applicability_unverified"}
                        connection_assessments[(field, expression_range)] = assessment
                        diagnostics["connection_assessments"].append(assessment)
                sibling_history: dict[tuple, list[int]] = {}
                structure_findings: dict[tuple[int, int], list[dict]] = {}
                for unit in units:
                    findings = []
                    unit_range = unit["source_range"]
                    parent = by_range.get(unit["parent_range"])
                    line_start = text.rfind("\n", 0, unit_range[0]) + 1
                    inline = bool(text[line_start:unit_range[0]].strip())
                    if unit["identification_status"] != "verified":
                        if inline:
                            findings.append({"code": "boundary_unverified", "source_range": unit_range})
                        expected_parents = {"paragraph": (None, "article"),
                                            "item": ("article", "paragraph"), "subitem": ("item",)}
                        parent_type = parent["unit_type"] if parent is not None else None
                        if (unit["unit_type"] in expected_parents
                                and parent_type not in expected_parents[unit["unit_type"]]
                                or parent is not None and parent["identification_status"] != "verified"):
                            findings.append({"code": "parent_unverified", "source_range": unit_range,
                                             "parent_range": unit["parent_range"], "parent_type": parent_type})
                    label = unit["unit_label"]
                    number = None
                    if not inline and unit["unit_type"] != "article":
                        if unit["unit_type"] == "paragraph":
                            number = ord(label) - ord("①") + 1
                        elif unit["unit_type"] == "item" and label[:-1].isdigit():
                            number = int(label[:-1])
                        elif unit["unit_type"] == "subitem":
                            number = "가나다라마바사아자차카타파하".index(label[0]) + 1
                        history = sibling_history.setdefault((unit["parent_range"], unit["unit_type"]), [])
                        if number is None:
                            findings.append({"code": "numbering_unsupported", "source_range": unit_range,
                                             "label": label})
                        else:
                            expected = history[-1] + 1 if history else 1
                            if number != expected:
                                code = ("numbering_duplicate" if number in history else
                                        "numbering_gap" if number > expected else "numbering_order")
                                findings.append({"code": code, "source_range": unit_range,
                                                 "expected": expected, "observed": number})
                            history.append(number)
                    if unit["identification_status"] != "verified" and not findings:
                        findings.append({"code": "structure_cause_unverified", "source_range": unit_range})
                    structure_findings[unit_range] = findings
                for unit in units:
                    if unit["identification_status"] == "verified":
                        continue
                    group_evidence = [finding for other in units
                                      if other["unit_type"] == unit["unit_type"]
                                      and other["parent_range"] == unit["parent_range"]
                                      for finding in structure_findings[other["source_range"]]
                                      if finding["code"] in {"numbering_gap", "numbering_duplicate",
                                                             "numbering_order", "numbering_unsupported"}]
                    if group_evidence:
                        structure_findings[unit["source_range"]] = [
                            finding for finding in structure_findings[unit["source_range"]]
                            if finding["code"] != "structure_cause_unverified"] + [
                            {"code": "numbering_group_unverified", "source_range": unit["source_range"],
                             "evidence": group_evidence}]
                if not units:
                    diagnostics["candidates"].append({"chunk_id": chunk.chunk_id, "search_rank": rank,
                        "unit_range": None, "structure_status": "unverified", "decision": "excluded",
                        "decision_trace": [{"stage": "structure_filter", "reason": "no_structural_unit"}]})
                for unit in units:
                    diagnostic = {"chunk_id": chunk.chunk_id, "search_rank": rank,
                                  "unit_type": unit["unit_type"], "unit_label": unit["unit_label"],
                                  "unit_range": unit["source_range"], "parent_range": unit["parent_range"],
                                  "explicit_law_required": bool(explicit_laws),
                                  "explicit_law_match": chunk.law_name in explicit_laws,
                                  "explicit_article_required": bool(explicit_articles),
                                  "explicit_article_match": article_match,
                                  "structure_status": unit["identification_status"],
                                  "structure_findings": structure_findings[unit["source_range"]],
                                  "concept_matches": None, "matched_roles": None, "keyword_matches": None,
                                  "priority": None, "connection_findings": [], "decision": "excluded",
                                  "introduction_status": "not_evaluated",
                                  "decision_trace": []}
                    diagnostics["candidates"].append(diagnostic)
                    if unit["unit_type"] not in {"paragraph", "item"} or unit["identification_status"] != "verified":
                        diagnostic["decision_trace"].append({"stage": "structure_filter", "reason":
                            "unsupported_unit_type" if unit["unit_type"] not in {"paragraph", "item"}
                            else "structure_unverified"})
                        continue
                    left, right = unit["source_range"]
                    body = text[left:right].lower()
                    matched_terms = {term for term in terms if len(term) >= 2
                                     and term not in explicit_laws and term not in explicit_articles
                                     and term.lower() in body}
                    matched_concepts = [concept for concept in concepts if any(
                        term and term.lower() in body for term in concept.get("search_terms", []))]
                    roles = {concept.get("role") for concept in matched_concepts if concept.get("role")}
                    diagnostic["concept_matches"] = [
                        {"concept_id": concept.get("concept_id"), "role": concept.get("role"),
                         "terms": [term for term in concept.get("search_terms", []) if term and term.lower() in body]}
                        for concept in matched_concepts]
                    diagnostic["matched_roles"] = sorted(roles)
                    diagnostic["keyword_matches"] = [{"term": term, "weight": float(weights.get(term, 1.0))}
                                                     for term in sorted(matched_terms)]
                    if not matched_terms and not matched_concepts:
                        diagnostic["decision_trace"].append({"stage": "relevance_filter", "reason": "no_term_or_concept_match"})
                        continue
                    if explicit_laws and chunk.law_name not in explicit_laws:
                        diagnostic["decision_trace"].append({"stage": "explicit_filter", "reason": "explicit_law_mismatch"})
                        continue
                    if explicit_articles and not article_match:
                        diagnostic["decision_trace"].append({"stage": "explicit_filter", "reason": "explicit_article_mismatch"})
                        continue
                    source_ranges = [unit["source_range"]]
                    diagnostic["introduction_status"] = "not_applicable"
                    parent = by_range.get(unit["parent_range"])
                    while parent is not None:
                        for introduction in detail["body_ranges"]:
                            if introduction["unit_range"] == parent["source_range"]:
                                source_ranges.append(introduction["source_range"])
                                diagnostic["introduction_status"] = "structurally_verified"
                                if introduction["identification_status"] != "verified":
                                    diagnostic["introduction_status"] = "unverified"
                                    diagnostic["connection_findings"].append({
                                        "code": "introduction_unverified", "source_range": introduction["source_range"],
                                        "triggers_existing_failure": False})
                        parent = by_range.get(parent["parent_range"])
                    source_ranges.sort()
                    excerpt_parts = []
                    trimmed_ranges = []
                    for start, end in source_ranges:
                        raw = text[start:end]
                        if not raw.strip():
                            continue
                        start += len(raw) - len(raw.lstrip())
                        end -= len(raw) - len(raw.rstrip())
                        trimmed_ranges.append((start, end))
                        excerpt_parts.append(re.sub(r"\s+", " ", text[start:end]))
                    uncertain = []
                    for field in ("qualifications", "references"):
                        for expression in detail[field]:
                            if any(start < expression["source_range"][1] and expression["source_range"][0] < end
                                   for start, end in trimmed_ranges):
                                diagnostic["connection_findings"].append({
                                    "code": "qualification_target_unverified" if field == "qualifications" else "reference_relation_unverified",
                                    "expression": expression.get("expression"), "source_range": expression["source_range"],
                                    "owner_range": expression.get("owner_range"),
                                    "target_range": expression.get("target_range"), "triggers_existing_failure": True,
                                    "assessment": connection_assessments[(field, expression["source_range"])]})
                        if any(any(start < expression["source_range"][1]
                                   and expression["source_range"][0] < end
                                   for start, end in trimmed_ranges) for expression in detail[field]):
                            uncertain.append("단서 연결 미확인" if field == "qualifications" else "참조 연결 미확인")
                    if any(other["identification_status"] != "verified"
                           and left <= other["source_range"][0] < right for other in units):
                        uncertain.append("하위 구조 경계 미확인")
                        diagnostic["connection_findings"].extend(
                            {"code": "child_structure_unverified", "source_range": other["source_range"],
                             "findings": structure_findings[other["source_range"]], "triggers_existing_failure": True}
                            for other in units if other["identification_status"] != "verified"
                            and left <= other["source_range"][0] < right)
                    purpose_order = condition_order = 0
                    purpose = {"status": "not_evaluated", "actions": [], "condition_conflicts": []}
                    if purpose_active:
                        # 하위 호나 괄호 안의 행위를 주절 행위로 승격하지 않는다.
                        own_body = next((part for part in detail["body_ranges"]
                                         if part["unit_range"] == unit["source_range"]), None)
                        clause_start, clause_end = own_body["source_range"] if own_body else (left, right)
                        visible_chars = []
                        bracket_stack = []
                        balanced = True
                        for char in text[clause_start:clause_end]:
                            if char == '"':
                                if bracket_stack and bracket_stack[-1] == '"':
                                    bracket_stack.pop()
                                else:
                                    bracket_stack.append(char)
                                visible_chars.append(" ")
                            elif char in "(（[<“‘":
                                bracket_stack.append(char)
                                visible_chars.append(" ")
                            elif char in ")）]>”’":
                                expected_open = {")": "(", "）": "（", "]": "[", ">": "<", "”": "“", "’": "‘"}[char]
                                if not bracket_stack or bracket_stack.pop() != expected_open:
                                    balanced = False
                                visible_chars.append(" ")
                            else:
                                visible_chars.append(" " if bracket_stack else char)
                        balanced = balanced and not bracket_stack
                        visible = "".join(visible_chars)
                        sentence_end = re.search(r"다만|。|\.(?=\s|$)", visible)
                        if sentence_end:
                            visible = visible[:sentence_end.start()]
                        action_matches = list(re.finditer(r"(신고|제출)(?:하여야|해야)\s*한다(?=[.。\s]|$)", visible))
                        actions = sorted({match.group(1) for match in action_matches})
                        deadline_match = bool(re.search(r"\d+\s*(?:개월|일|월|년)|이내|까지|기한", visible))
                        heading = text[:units[1]["source_range"][0]] if len(units) > 1 and units[0]["unit_type"] == "article" else ""
                        conflicts = [condition for condition in excluded_conditions
                                     if len(actions) == 1 and condition["action"] == actions[0]
                                     and condition["object"] in (visible + heading)]
                        status = "unresolved"
                        if balanced and len(requested_actions) == len(actions) == 1 and not question_condition_unknown:
                            if conflicts:
                                status = "condition_conflict"
                                condition_order = -1
                            elif requested_actions == actions:
                                if not deadline_requested or deadline_match:
                                    status = "matched"
                                    purpose_order = 1
                            else:
                                status = "mismatched"
                        if status != "matched":
                            uncertain.append("질문 목적·조건 대응 미확인")
                        purpose = {"status": status, "actions": actions, "deadline_match": deadline_match,
                                   "condition_conflicts": conflicts,
                                   "action_ranges": [(clause_start + match.start(), clause_start + match.end())
                                                     for match in action_matches],
                                   "scope_balanced": balanced,
                                   "legal_applicability": "unverified"}
                        diagnostic["decision_trace"].append({"stage": "purpose_check", "reason": status})
                    diagnostic["purpose"] = purpose
                    rule_ok = bool((article_match or (required_roles & roles
                                                     and matched_concepts and matched_terms))
                                   and not uncertain)
                    candidates.append({"chunk_id": chunk.chunk_id, "search_rank": rank,
                                       "input_no": None, "law_name": chunk.law_name,
                                       "title": chunk.title or chunk.article or "제목 없음",
                                       "required": False, "ranges": trimmed_ranges,
                                       "excerpt": " ... ".join(excerpt_parts),
                                       "roles": sorted(roles), "rule_ok": rule_ok,
                                       "selection_reasons": uncertain or ["명시 조문·본문 일치" if article_match else "기존 개념·본문 후보"],
                                       "priority": (article_match, len(matched_concepts),
                                                    sum(float(weights.get(term, 1.0)) for term in matched_terms),
                                                    -rank, -left)})
                    diagnostic["legacy_priority"] = candidates[-1]["priority"]
                    if purpose_active:
                        previous_priority = candidates[-1]["priority"]
                        candidates[-1]["priority"] = (previous_priority[0], purpose_order, condition_order,
                                                       *previous_priority[1:])
                    diagnostic["priority"] = candidates[-1]["priority"]
                    diagnostic["rule_ok"] = rule_ok
                    diagnostic["decision"] = "not_reserved"
                    diagnostic["decision_trace"].append({"stage": "candidate_ready", "reason": "existing_priority_computed"})
                    candidate_diagnostics[id(candidates[-1])] = diagnostic
            candidates.sort(key=lambda block: block["priority"], reverse=True)
            for order, block in enumerate(candidates, 1):
                candidate_diagnostics[id(block)]["selection_order"] = order
            if not needed_laws and candidates:
                needed_laws = [candidates[0]["law_name"]]
            selected: list[dict] = []
            for law in needed_laws:
                match = next((block for block in candidates if block["law_name"] == law), None)
                diagnostics["reservation_events"].append({"stage": "law_reservation", "law_name": law,
                    "chosen": {"chunk_id": match["chunk_id"], "unit_range": candidate_diagnostics[id(match)]["unit_range"]}
                    if match is not None else None})
                if match is None:
                    reasons.append("필요 법령 후보 미확보: " + law)
                    diagnostics["failures"].append({"stage": "law_reservation", "condition": "required_law_missing",
                                                    "law_name": law})
                elif match not in selected:
                    match["required"] = True
                    selected.append(match)
                    candidate_diagnostics[id(match)]["decision_trace"].append(
                        {"stage": "law_reservation", "reason": "first_candidate_for_law"})
            # 역할별 필수 후보는 예산 검사 전에 예약한다.
            covered_roles = {role for block in selected for role in block["roles"]}
            for role in sorted(required_roles - covered_roles):
                match = next((block for block in candidates if role in block["roles"]), None)
                if match is None:
                    reasons.append("필수 역할 후보 미확보: " + role)
                    diagnostics["failures"].append({"stage": "role_reservation", "condition": "required_role_missing", "role": role})
                elif match not in selected:
                    match["required"] = True
                    selected.append(match)
                    candidate_diagnostics[id(match)]["decision_trace"].append(
                        {"stage": "role_reservation", "reason": "first_candidate_for_role", "role": role})
            for block in candidates:
                if block in selected:
                    continue
                winners = [event["chosen"] for event in diagnostics["reservation_events"]
                           if event["law_name"] == block["law_name"] and event["chosen"] is not None]
                if winners:
                    candidate_diagnostics[id(block)]["decision_trace"].append(
                        {"stage": "law_reservation", "reason": "not_first_for_law", "reserved_candidates": winners})
            if not selected:
                reasons.append("항·호 수준 직접 근거를 선정하지 못했습니다.")
                diagnostics["failures"].append({"stage": "reservation", "condition": "no_selected_candidate"})
            unresolved = bool(reasons or any(
                any("미확인" in reason for reason in block["selection_reasons"]) for block in selected))
            identification = "unresolved" if unresolved else (
                "rule_selected" if all(block["rule_ok"] for block in selected) else "unverified")
            if identification == "unverified":
                reasons.append("키워드 후보만으로 핵심 근거 선정 성공을 확정하지 않습니다.")
                diagnostics["failures"].append({"stage": "identification", "condition": "insufficient_rule_support"})
            for block in selected:
                reasons.extend(reason for reason in block["selection_reasons"] if "미확인" in reason)
                candidate_diagnostics[id(block)]["decision"] = "selected_required"
            connection_failures = [
                {"stage": "connection_check", "condition": finding["code"], "chunk_id": block["chunk_id"],
                 "unit_range": candidate_diagnostics[id(block)]["unit_range"], "evidence": finding}
                for block in selected for finding in candidate_diagnostics[id(block)]["connection_findings"]
                if finding["triggers_existing_failure"]]
            diagnostics["failures"] = connection_failures + diagnostics["failures"]
            diagnostics["first_failure"] = next(iter(diagnostics["failures"]), None)
            if not selected:
                return {"input_version": "a3b-v1", "context": None,
                        "identification": identification, "budget": "not_evaluated",
                        "char_limit": limit, "char_count": None, "blocks": [],
                        "reasons": list(dict.fromkeys(reasons)), "diagnostics": diagnostics}
            assembled = ""
            budget = "not_evaluated"
            # 필수 묶음은 먼저 전부 조립하고, 선택 근거는 남는 예산에만 추가한다.
            optional = [block for block in candidates if block not in selected and block["rule_ok"]]
            for block in candidates:
                if block not in selected and not block["rule_ok"]:
                    candidate_diagnostics[id(block)]["decision_trace"].append(
                        {"stage": "optional_filter", "reason": "rule_not_satisfied"})
            for addition in [None, *optional]:
                if addition is not None and any(
                    block["chunk_id"] == addition["chunk_id"]
                    and any(left < other_right and other_left < right
                            for left, right in block["ranges"]
                            for other_left, other_right in addition["ranges"])
                    for block in selected
                ):
                    candidate_diagnostics[id(addition)]["decision_trace"].append(
                        {"stage": "optional_filter", "reason": "source_overlap"})
                    continue
                trial = selected + ([addition] if addition is not None else [])
                rendered = [f"[{index}]\n법령명: {block['law_name'] or '법령명 없음'}\n"
                            f"조문명: {block['title']}\n내용:\n{block['excerpt']}"
                            for index, block in enumerate(trial, 1)]
                trial_context = context_prefix + "\n\n".join(rendered) + reserved_context + context_suffix
                if len(trial_context) > limit:
                    if addition is None:
                        budget = "exceeded"
                        reasons.append("필수 근거와 외곽·예약 문자열의 합계가 문자 예산을 초과합니다.")
                        diagnostics["failures"].append({"stage": "budget", "condition": "required_budget_exceeded",
                                                        "trial_chars": len(trial_context), "char_limit": limit})
                        break
                    candidate_diagnostics[id(addition)]["decision_trace"].append(
                        {"stage": "budget", "reason": "optional_budget_exceeded",
                         "trial_chars": len(trial_context), "char_limit": limit})
                    continue
                budget = "within"
                assembled = trial_context
                if addition is not None:
                    selected.append(addition)
                    candidate_diagnostics[id(addition)]["decision"] = "selected_optional"
                    candidate_diagnostics[id(addition)]["decision_trace"].append(
                        {"stage": "budget", "reason": "optional_fits"})
            if budget == "exceeded":
                for block in optional:
                    candidate_diagnostics[id(block)]["decision_trace"].append(
                        {"stage": "budget", "reason": "optional_not_evaluated_required_budget_failed"})
            for index, block in enumerate(selected, 1):
                block["input_no"] = index if budget == "within" else None
                block.pop("priority")
            usable = identification == "rule_selected" and budget == "within"
            for assessment in diagnostics["connection_assessments"]:
                # 기존 발췌의 양끝 공백 제거를 내용 누락으로 오인하지 않는다.
                start, end = assessment["preservation_range"]
                assessment["selected_block_preserved"] = any(
                    block["chunk_id"] == assessment["chunk_id"]
                    and any(left <= start < end <= right for left, right in block["ranges"]) for block in selected)
                assessment["effective_context_preserved"] = usable and assessment["selected_block_preserved"]
                assessment["selected_failure"] = any(
                    failure.get("chunk_id") == assessment["chunk_id"]
                    and failure.get("evidence", {}).get("source_range") == assessment["source_range"]
                    and failure.get("condition") == ("qualification_target_unverified"
                         if assessment["expression_type"] == "qualification" else "reference_relation_unverified")
                    for failure in diagnostics["failures"])
                if assessment["selected_block_preserved"]:
                    assessment["decision"] = "preserved_but_unverified"
            diagnostics["budget_exceeded"] = budget == "exceeded"
            diagnostics["first_failure"] = next(iter(diagnostics["failures"]), None)
            diagnostics["selected_candidates"] = [
                {"chunk_id": block["chunk_id"], "unit_range": candidate_diagnostics[id(block)]["unit_range"],
                 "required": block["required"]} for block in selected]
            return {"input_version": "a3b-v1", "context": assembled if usable else None,
                    "identification": identification, "budget": budget, "char_limit": limit,
                    "char_count": len(assembled) if usable else None,
                    "blocks": selected, "reasons": list(dict.fromkeys(reasons)), "diagnostics": diagnostics}
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
        *,
        return_details: bool = False,
    ) -> str | dict:
        normalized = re.sub(r"\s+", " ", text).strip()
        selected_spans: list[tuple[int, int]] = []
        prefix = suffix = ""
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
            excerpt = " ".join(part for part in relevant_parts if part)
            selected_spans = [
                (0, first_item) if first_item > 0 else (0, 0),
                (third_item, third_end),
                (fifth_item, fifth_end) if fifth_item > 0 else (0, 0),
            ]
        elif len(normalized) <= max_chars:
            excerpt = normalized
            selected_spans = [(0, len(normalized))]
        else:
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
                prefix = "..."
                excerpt = prefix + excerpt
            if end < len(normalized):
                suffix = "..."
                excerpt += suffix
            selected_spans = [(start, end)]

        if not return_details:
            return excerpt

        # 정규화 문자별로 원문 범위를 보관한다.
        source_map: list[tuple[int, int]] = []
        mapped_chars: list[str] = []
        for match in re.finditer(r"\s+|[^\s]", text):
            mapped_chars.append(" " if match.group().isspace() else match.group())
            source_map.append((match.start(), match.end()))
        left = 1 if mapped_chars and mapped_chars[0] == " " else 0
        right = len(mapped_chars) - (1 if mapped_chars and mapped_chars[-1] == " " else 0)
        valid = "".join(mapped_chars[left:right]) == normalized
        source_map = source_map[left:right]
        segments: list[dict] = []
        ranges: list[tuple[int, int]] = []
        cursor = 0
        if prefix:
            segments.append({"excerpt_range": (0, len(prefix)),
                             "source_range": None, "kind": "generated"})
            cursor = len(prefix)
        for span_start, span_end in selected_spans:
            # 슬라이스와 strip 이후 실제 남은 경계를 사용한다.
            actual_start, actual_end, _ = slice(span_start, span_end).indices(len(normalized))
            raw_piece = normalized[actual_start:actual_end]
            piece = raw_piece.strip()
            if not piece:
                continue
            piece_start = actual_start + len(raw_piece) - len(raw_piece.lstrip())
            piece_end = piece_start + len(piece)
            if not (0 <= piece_start < piece_end <= len(source_map)):
                valid = False
                break
            if ranges:
                segments.append({"excerpt_range": (cursor, cursor + 1),
                                 "source_range": None, "kind": "generated"})
                valid = valid and excerpt[cursor:cursor + 1] == " "
                cursor += 1
            source_range = (source_map[piece_start][0], source_map[piece_end - 1][1])
            output_end = cursor + len(piece)
            valid = valid and 0 <= source_range[0] < source_range[1] <= len(text)
            valid = valid and re.sub(r"\s+", " ", text[source_range[0]:source_range[1]]) == piece
            valid = valid and excerpt[cursor:output_end] == piece
            ranges.append(source_range)
            segments.append({"excerpt_range": (cursor, output_end),
                             "source_range": source_range, "kind": "source"})
            cursor = output_end
        if suffix:
            segments.append({"excerpt_range": (cursor, cursor + len(suffix)),
                             "source_range": None, "kind": "generated"})
            valid = valid and excerpt[cursor:cursor + len(suffix)] == suffix
            cursor += len(suffix)
        valid = valid and cursor == len(excerpt)
        # 소문자화로 길이가 변한 검색 창의 위치 의미는 추측하지 않는다.
        if (prefix or suffix) and len(normalized.lower()) != len(normalized):
            valid = False
        if not valid:
            segments = []

        # 구조는 정규화 전 원문에서만 읽고, 표시되지 않은 번호는 만들지 않는다.
        units: list[dict] = []
        levels = {"article": 0, "paragraph": 1, "item": 2, "subitem": 3}
        marker_pattern = (
            r"(?m)^[ \t]*(?:"
            r"(?P<article>제\s*\d+\s*조(?:\s*의\s*\d+)?)"
            r"[ \t]*(?:\([^\n)]*\)|(?=[ \t]*$))|"
            r"(?P<paragraph>[①-⑳])|"
            r"(?P<item>\d+(?:의\d+)?\.)(?=[ \t]+[^\s])|"
            r"(?P<subitem>[가나다라마바사아자차카타파하]\.)(?=[ \t]+[^\s]))"
        )
        markers = list(re.finditer(marker_pattern, text))
        stack: list[int] = []
        parents: list[int | None] = []
        sibling_numbers: dict[tuple[int | None, str], int] = {}
        for marker in markers:
            unit_type = next(name for name in levels if marker.group(name) is not None)
            level = levels[unit_type]
            start = marker.start(unit_type)
            while stack and levels[units[stack[-1]]["unit_type"]] >= level:
                units[stack.pop()]["source_range"][1] = start
            parent_index = stack[-1] if stack else None
            parent_type = units[parent_index]["unit_type"] if parent_index is not None else None
            label = marker.group(unit_type)
            status = "verified"
            if unit_type != "article":
                if unit_type == "paragraph":
                    number = ord(label) - ord("①") + 1
                    parent_ok = parent_type in (None, "article")
                elif unit_type == "item":
                    number = int(label[:-1]) if label[:-1].isdigit() else None
                    parent_ok = parent_type in ("article", "paragraph")
                else:
                    number = "가나다라마바사아자차카타파하".index(label[0]) + 1
                    parent_ok = parent_type == "item"
                key = (parent_index, unit_type)
                previous = sibling_numbers.get(key, 0)
                if not parent_ok or number != previous + 1:
                    status = "unverified"
                if number is not None:
                    sibling_numbers[key] = number
                if parent_index is not None and units[parent_index]["identification_status"] != "verified":
                    status = "unverified"
            units.append({"unit_type": unit_type, "unit_label": label,
                          "source_range": [start, len(text)], "parent_range": None,
                          "identification_status": status, "content_start": marker.end()})
            parents.append(parent_index)
            stack.append(len(units) - 1)

        # 번호 누락·중복이 있는 같은 계층을 일부만 확정하지 않는다.
        uncertain_groups = {(parents[index], unit["unit_type"])
                            for index, unit in enumerate(units)
                            if unit["identification_status"] == "unverified"}
        for index, unit in enumerate(units):
            parent_index = parents[index]
            if ((parent_index, unit["unit_type"]) in uncertain_groups
                    or (parent_index is not None
                        and units[parent_index]["identification_status"] == "unverified")):
                unit["identification_status"] = "unverified"

        body_ranges: list[dict] = []
        for index, unit in enumerate(units):
            unit["source_range"] = tuple(unit["source_range"])
        for index, unit in enumerate(units):
            parent_index = parents[index]
            if parent_index is not None:
                unit["parent_range"] = units[parent_index]["source_range"]
            children = [child for child, parent in enumerate(parents) if parent == index]
            body_end = units[children[0]]["source_range"][0] if children else unit["source_range"][1]
            body_start = unit.pop("content_start")
            if text[body_start:body_end].strip():
                body_ranges.append({"source_range": (body_start, body_end),
                                    "unit_range": unit["source_range"],
                                    "kind": "common_introduction" if children else "body",
                                    "identification_status": unit["identification_status"]})
            unit["excerpt_overlaps"] = [
                (max(left, unit["source_range"][0]), min(right, unit["source_range"][1]))
                for left, right in ranges
                if max(left, unit["source_range"][0]) < min(right, unit["source_range"][1])
            ] if valid else None
        first_start = units[0]["source_range"][0] if units else len(text)
        if text[:first_start].strip():
            body_ranges.insert(0, {"source_range": (0, first_start), "unit_range": None,
                                   "kind": "body", "identification_status": "unverified"})

        # 줄 중간 번호는 인용·일반 문장일 수 있으므로 계층에 연결하지 않는다.
        known_starts = {unit["source_range"][0] for unit in units}
        for marker in re.finditer(r"[①-⑳]|(?<!\S)(?:\d+\.|[가나다라마바사아자차카타파하]\.)(?=\s)", text):
            if marker.start() in known_starts:
                continue
            label = marker.group()
            unit_type = "paragraph" if label in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳" else (
                "item" if label[0].isdigit() else "subitem")
            units.append({"unit_type": unit_type, "unit_label": label,
                          "source_range": (marker.start(), marker.end()), "parent_range": None,
                          "identification_status": "unverified",
                          "excerpt_overlaps": [(max(left, marker.start()), min(right, marker.end()))
                                               for left, right in ranges
                                               if max(left, marker.start()) < min(right, marker.end())] if valid else None})
        units.sort(key=lambda unit: unit["source_range"][0])

        qualifications: list[dict] = []
        for marker in re.finditer(r"다만|(?<![가-힣])단(?=[ \t]*[,，])|예외|제외", text):
            containers = [unit for unit in units
                          if unit["identification_status"] == "verified"
                          and unit["source_range"][0] <= marker.start() < unit["source_range"][1]]
            owner = min(containers, key=lambda unit: unit["source_range"][1] - unit["source_range"][0]) if containers else None
            expression_end = owner["source_range"][1] if owner else len(text)
            sentence_end = re.search(r"[。\n]|\.(?=\s|$)", text[marker.end():expression_end])
            if sentence_end:
                expression_end = marker.end() + sentence_end.end()
            qualifications.append({"expression": text[marker.start():expression_end],
                                   "marker_range": (marker.start(), marker.end()),
                                   "source_range": (marker.start(), expression_end),
                                   "owner_range": owner["source_range"] if owner else None,
                                   "target_range": None, "identification_status": "unverified"})
        references: list[dict] = []
        for match in re.finditer(r"(?:「[^」]+」\s*)?제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제\s*\d+\s*(?:항|호|목))*", text):
            if match.start() in known_starts and any(
                unit["unit_type"] == "article" and unit["source_range"][0] == match.start() for unit in units
            ):
                continue
            references.append({"expression": match.group(), "source_range": (match.start(), match.end()),
                               "identification_status": "unverified"})
        return {
            "excerpt": excerpt,
            "position_status": "verified" if valid else "unverified",
            "ranges": ranges if valid else None,
            "segments": segments,
            "reason": None if valid else "원문 위치 대응을 검증하지 못했습니다.",
            "structure_units": units,
            "body_ranges": body_ranges,
            "qualifications": qualifications,
            "references": references,
        }

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
        has_business_income_context = any(
            term in search_text
            for term in ("사업소득", "프리랜서", "3.3", "3.3%")
        )
        has_withholding_context = "원천징수" in search_text

        return has_business_income_context and has_withholding_context

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
    def build_supplemental_conditions(
        cls, conditions: dict, *, retrieved_chunks: list[LawChunk] | None = None,
        visited: set[tuple] | None = None,
    ) -> list[dict]:
        if retrieved_chunks is not None:
            # The result-driven phase returns dependencies only, never repeats
            # the existing question-driven supplemental searches below.
            names = conditions.get("law_names", [])
            question = conditions.get("original_question", "")
            if not names or any(name not in _CANONICAL_LAW_NAME_SET for name in names):
                return []
            if not re.search(r"요건|범위|제한|방법|기준|판단|비교", question) or re.search(r"(?:어떤|어느)\s*(?:조문|규정|법령)", question):
                return []
            generic = {"사업", "법인", "개인사업자", "법인사업자", "각각", "해당", "여부", "규정", "기준", "범위",
                       "요건", "무엇", "알려", "판단", "비교", "적용", "확인", "인정받", "있는", "없게"}
            labels = {hint for values in cls.DOMAIN_HINTS.values() for hint in values if hint.endswith("세")}
            terms = cls.deduplicate_keep_order([
                re.sub(r"(?:하여|하는지|하는|할|하나|했다면|되어|되는지)$", "", term)
                for term in cls.extract_keywords(question)
                if term not in generic and term not in labels and term not in _CANONICAL_LAW_NAME_SET
                and not re.fullmatch(r"제?\d+(?:조|항|호).*", term)
            ])
            seeds: list[tuple[tuple[int, int], LawChunk, list[str]]] = []
            for chunk in retrieved_chunks:
                text = chunk.content or ""
                if chunk.law_name not in names or not re.match(r"^\s*제\d+조", text):
                    continue
                if not re.search(r"대통령령|[가-힣]+부령|총리령|「[^」]+」\s*제\d+조", text):
                    continue
                matches = [term for term in terms if len(term) >= 2 and term in (chunk.title or "")]
                has_specific_topic = any(len(term) >= 4 for term in matches)
                has_transaction = re.search(r"경우|하면|했다면|하였다면|사용했다|제공하면", question)
                # Title matches order requests; missing title terms alone cannot
                # exclude a dependency explained in a later paragraph or item.
                dependency_blocks = re.split(r"(?=[①-⑳])|\n(?=\s*\d+(?:의\d+)?\.\s)", text)
                body_topic = any(
                    re.search(r"대통령령|[가-힣]+부령|총리령|「[^」]+」", block)
                    and any(len(term) >= 4 and term in block for term in terms)
                    for block in dependency_blocks
                )
                if not (has_specific_topic or (len(matches) >= 2 and has_transaction) or body_topic):
                    continue
                relevance = (sum(len(term) for term in matches),
                             sum(term in text for term in terms if term not in matches and len(term) >= 2))
                seeds.append((relevance, chunk, dependency_blocks))
            plans: list[dict] = []
            planned = set(visited or ())
            # Inspect all eligible retrieved seeds. Request limits apply only to
            # the generated lookups; non-delegating/invalid sources consume none.
            for _, seed, source_units in sorted(seeds, key=lambda item: item[0], reverse=True):
                text = seed.content or ""
                parent_article = cls.extract_article_numbers(text[:80])[0]
                topic = [term for term in cls.extract_keywords(seed.title or "")
                         if len(term) >= 2 and not re.fullmatch(r"제?\d+(?:조|항|호).*", term)
                         and term not in {"등", "특례", "규정", "범위", "계산"}]
                targets = []
                if seed.law_name.endswith(" 시행령"):
                    if re.search(r"[가-힣]+부령|총리령", text):
                        targets.append((seed.law_name.removesuffix(" 시행령") + " 시행규칙", "영"))
                elif not seed.law_name.endswith(" 시행규칙"):
                    if "대통령령" in text:
                        targets.append((seed.law_name + " 시행령", "법"))
                    if re.search(r"[가-힣]+부령|총리령", text):
                        targets.append((seed.law_name + " 시행규칙", "법"))
                for target, alias in targets:
                    key = ("delegation", seed.law_name, target, parent_article)
                    if target not in _CANONICAL_LAW_NAME_SET or target in names or key in planned:
                        continue
                    instrument = "decree" if target.endswith(" 시행령") else "rule"
                    mandate = r"대통령령" if instrument == "decree" else r"(?:[가-힣]+부령|총리령)"
                    delegation_basis = []
                    for unit_index, unit in enumerate(source_units):
                        if not re.search(mandate + r"(?:으로|에서)정", re.sub(r"\s+", "", unit)):
                            continue
                        subjects = cls.deduplicate_keep_order([
                            term for term in cls.extract_keywords(unit)
                            if len(term) >= 2 and term not in generic
                            and term not in {"필요", "필요한", "사항", "정하", "정한", "정하는", "말한다", "그밖", "이하", "규정", "의한", "따른"}
                            and not term.endswith(("령", "부령"))
                            and not re.fullmatch(r"제?\d+(?:조|항|호).*", term)
                        ])
                        if subjects:
                            marker = re.match(r"\s*([①-⑳]|\d+(?:의\d+)?\.)", unit)
                            delegation_basis.append({"unit": marker.group(1) if marker else str(unit_index),
                                                     "instrument": instrument, "subject_terms": subjects})
                    if not delegation_basis:
                        continue
                    planned.add(key)
                    keywords = cls.deduplicate_keep_order([parent_article] + topic)
                    plans.append({"kind": "delegation", "request_key": key,
                                  "seed_chunk_id": seed.chunk_id, "from_law": seed.law_name,
                                  "parent_article": parent_article, "parent_alias": alias,
                                  "delegation_basis": delegation_basis,
                                  "law_names": [target], "keywords": keywords,
                                  "rewritten_query": cls.build_rewritten_query(
                                      question=question, intent=conditions.get("intent", ""), action="",
                                      keywords=keywords, law_names=[], article_numbers=[],
                                  )})
                # Pair the law name with the article in the same citation span.
                # Follow subordinate-law dependencies, not all mentioned statutes.
                for match in re.finditer(r"「([^」]+)」\s*(제\s*\d+\s*조(?:\s*의\s*\d+)?)", text):
                    extracted = cls.extract_law_names(re.sub(r"\s+", " ", match.group(1)))
                    if len(extracted) != 1 or extracted[0] not in _CANONICAL_LAW_NAME_SET:
                        continue
                    target = extracted[0]
                    article = cls.extract_article_numbers(re.sub(r"\s+", "", match.group(2)))[0]
                    key = ("reference", target, article)
                    if not target.endswith((" 시행령", " 시행규칙")) or target in names or key in planned:
                        continue
                    # Newline wrapping does not end a legal paragraph. Use the
                    # article heading, paragraph/item markers and blank lines.
                    boundaries = {0, len(text)}
                    offset = 0
                    for line_index, line in enumerate(text.splitlines(keepends=True)):
                        if line_index == 0 and re.match(r"^\s*제\d+조", line):
                            boundaries.add(offset + len(line))
                        elif not line.strip() or re.match(r"^\s*(?:[①-⑳]|\d+(?:의\d+)?\.\s)", line):
                            boundaries.add(offset)
                        offset += len(line)
                    boundaries.update(marker.start() for marker in re.finditer(r"[①-⑳]", text))
                    start = max(value for value in boundaries if value <= match.start())
                    end = min(value for value in boundaries if value > match.start())
                    citation_context = text[start:end]
                    specific_terms = [term for term in terms if len(term) >= 4]
                    if not any(term[:max(2, len(term) - 2)] in citation_context for term in specific_terms):
                        continue
                    tail = text[match.end():end]
                    if not re.search(r"따라|따른|준용|인정|사유|정하는", tail):
                        continue
                    planned.add(key)
                    plans.append({"kind": "reference", "request_key": key,
                                  "seed_chunk_id": seed.chunk_id, "from_law": seed.law_name,
                                  "parent_article": parent_article, "target_article": article,
                                  "reference_text": match.group(0), "law_names": [target],
                                  "keywords": [article],
                                  "rewritten_query": cls.build_rewritten_query(
                                      question=question, intent=conditions.get("intent", ""), action="",
                                      keywords=[], law_names=[], article_numbers=[article],
                                  )})
            return plans

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
        if cls.is_nominee_business_question(conditions["original_question"]):
            law_names = cls.prepend_unique(
                ["국세기본법"],
                conditions["law_names"],
            )
            supplemental_conditions.append(
                {
                    "law_names": law_names,
                    "keywords": cls.NOMINEE_BUSINESS_QUERY_HINTS,
                    "rewritten_query": (
                        "국세기본법 제14조 실질과세 과세 대상 거래의 귀속이 "
                        "명의일 뿐이고 사실상 귀속되는 자 납세의무자"
                    ),
                }
            )
        return supplemental_conditions

    @staticmethod
    def is_nominee_business_question(question: str) -> bool:
        compact_question = re.sub(r"\s+", "", question or "")
        has_nominee = any(
            term in compact_question
            for term in ("명의상", "명의자", "명의를빌려", "명의를빌린", "사업자명의")
        )
        has_actual_operator = any(
            term in compact_question
            for term in ("실제운영자", "실질사업자", "실질대표", "사실상귀속")
        )
        asks_taxpayer = any(
            term in compact_question
            for term in ("납세의무자", "납세의무", "세금", "부가가치세", "누구")
        )
        return has_nominee and has_actual_operator and asks_taxpayer

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
                        domain_scores[domain] += (
                            2 if hint in ChunkSearchService.EXPLICIT_DOMAIN_HINTS else 1
                        )

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
        canonical_matches = list(_CANONICAL_LAW_PATTERN.finditer(question))
        positioned = [
            (match.start(), _CANONICAL_LAW_NAMES[int(match.lastgroup[1:])])
            for match in canonical_matches
        ]
        for match in re.finditer(r"[가-힣A-Za-z]+(?:법|시행령|시행규칙)", question):
            if any(full.start() <= match.start() and match.end() <= full.end() for full in canonical_matches):
                continue
            if match.start() and re.match(r"[0-9A-Za-z가-힣]", question[match.start() - 1]):
                continue
            if not _LAW_NAME_END.match(question, match.end()):
                continue
            # DB absence alone does not establish that an explicit name is invalid.
            positioned.append((match.start(), match.group()))
        positioned.sort(key=lambda item: item[0])
        return ChunkSearchService.deduplicate_keep_order([name for _, name in positioned])

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
        explicit_tax_domains: list[str] | None = None,
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

        requirement_facets: list[dict] = []
        facet_scores: dict[str, list[float]] = {}
        selection_trace: list[dict] = []
        domains = ChunkSearchService.deduplicate_keep_order(explicit_tax_domains or [])
        if len(ranked) > top_k:
            # Requirements use the original question, never expanded search keywords.
            # Consume the whole canonical name and its particle before tokenization.
            law_spans = list(_CANONICAL_LAW_PATTERN.finditer(query_text))
            clean_question = query_text
            for match in reversed(law_spans):
                particle = re.match(_LAW_NAME_PARTICLE + _LAW_NAME_DELIMITER, query_text[match.end():])
                end = match.end() + (particle.end() if particle else 0)
                clean_question = clean_question[:match.start()] + " " * (end - match.start()) + clean_question[end:]
            # A separate instruction excluding a scope is not positive evidence.
            clean_question = re.sub(
                r"(?<=[.?!])[^.?!]*(?:제외합니다|제외해 주세요|제외해주세요)[.?!]?",
                lambda match: " " * len(match.group()), clean_question,
            )
            clauses: list[tuple[str, list[str]]] = []
            if len(domains) > 1:
                anchors: list[tuple[int, int, str]] = []
                for domain in domains:
                    family = ChunkSearchService.infer_law_names(domain)
                    for match in law_spans:
                        if _CANONICAL_LAW_NAMES[int(match.lastgroup[1:])] in family:
                            anchors.append((match.start(), match.end(), domain))
                    hints = (ChunkSearchService.DOMAIN_HINTS.get(domain, set()) | {domain}) & (
                        ChunkSearchService.EXPLICIT_DOMAIN_HINTS | {domain}
                    )
                    for hint in hints:
                        if not hint.endswith("세"):
                            continue
                        pattern = r"[ \t]*".join(re.escape(part) for part in hint.split())
                        for match in re.finditer(pattern, query_text):
                            if not any(start <= match.start() < end for start, end, _ in anchors):
                                particle = re.match(_LAW_NAME_PARTICLE + _LAW_NAME_DELIMITER, query_text[match.end():])
                                end = match.end() + (particle.end() if particle else 0)
                                anchors.append((match.start(), end, domain))
                anchors.sort()
                shared_context = clean_question[:anchors[0][0]] if anchors else ""
                # A negated premise is not positive relevance evidence. Without a
                # scope-aware negation model, do not spread it into every facet.
                if re.search(r"아닌|않는|아니하는|없(?:다고|는|을)|아니라고", shared_context):
                    shared_context = ""
                for index, (start, end, domain) in enumerate(anchors):
                    stop = anchors[index + 1][0] if index + 1 < len(anchors) else len(query_text)
                    clauses.append((clean_question[end:stop] + " " + shared_context,
                                    ChunkSearchService.infer_law_names(domain)))
            elif law_names:
                # A coordination is necessary, but not sufficient, for same-law facets.
                # Do not infer several requirements from every ordinary single question.
                families = [family for family in ChunkSearchService.DOMAIN_TO_LAWS.values()
                            if set(law_names).issubset(family)]
                family = families[0] if families else law_names if len(law_names) == 1 else []
                if family:
                    parts = re.split(r"(?<=[가-힣])(?:과|와)(?=\s)|[,;]\s*(?:그리고|또한)\s+|\s+(?:및|그리고|또한)\s+", clean_question)
                    if len(parts) > 1 and all(
                        re.search(r"기준|시기|기한|제한|요건|원칙|범위|의무", part) for part in parts
                    ):
                        clauses = [(part, family) for part in parts]

            # Discourse, particles and request verbs carry no evidential meaning.
            # Keep this local to reranking; search keywords and search scope are unchanged.
            discourse = {
                "각각", "서로", "각", "여러", "차례", "이내", "대해", "대하여", "대한", "관련",
                "해당", "여부", "경우", "무엇", "어떤", "어떻게", "알려", "주세요", "확인",
                "싶습니다", "싶습", "대상", "아닌", "있고", "규정", "원칙", "기준", "범위", "판단", "판단하", "판단하나",
                "적용", "적용하", "적용되", "따라", "법에서", "법상", "에서는", "에서", "으로",
            }
            term_sets: list[tuple[list[str], list[str]]] = []
            facet_term_scores: dict[str, list[dict[str, float]]] = {}
            tax_labels = {hint for hints in ChunkSearchService.DOMAIN_HINTS.values()
                          for hint in hints if hint.endswith("세")}
            tax_labels.update(label for name in law_names for label in re.findall(r"[가-힣]+세", name))
            for text, family in clauses:
                terms = []
                # Reuse the matched concept's surface, without expanding a filing
                # request into other duties such as payment or unrelated deadlines.
                legal_terms = [value for concept in extract_legal_concepts(text)
                               for value in ChunkSearchService.extract_keywords(concept.get("surface", ""))]
                for term in ChunkSearchService.extract_keywords(text) + legal_terms:
                    term = re.sub(r"(?:하|되)$", "", term.lower())
                    if len(term) < 2 or term in discourse or term in tax_labels or term in _CANONICAL_LAW_NAME_SET:
                        continue
                    if re.fullmatch(r"제?\d+(?:조|항|호).*", term):
                        continue
                    terms.append(term)
                term_sets.append((ChunkSearchService.deduplicate_keep_order(terms), family))
            document_texts = {
                chunk.chunk_id: ((chunk.title or "").lower(), (chunk.content or "").lower())
                for chunk in ranked
            }
            for index, (terms, family) in enumerate(term_sets):
                other_terms = {term for other, (values, _) in enumerate(term_sets) if other != index for term in values}
                core_terms = [term for term in terms if term not in other_terms]
                if not core_terms:
                    continue
                weights: dict[str, float] = {}
                for term in terms:
                    frequency = sum(term in title or term in content for title, content in document_texts.values())
                    if frequency:
                        weights[term] = math.log(1.0 + (len(ranked) - frequency + 0.5) / (frequency + 0.5))
                if not weights:
                    continue
                coverage: dict[str, float] = {}
                term_coverage: dict[str, dict[str, float]] = {}
                for chunk in ranked:
                    title, content = document_texts[chunk.chunk_id]
                    # A single local passage must support a body-only match. Scattered
                    # words across a long statute do not jointly satisfy a requirement.
                    passages = re.split(r"[.。\n]+|[①-⑳]", content)
                    # An attached proviso qualifies the preceding rule; do not turn
                    # its exception into an unrelated passage.
                    joined_passages: list[str] = []
                    for passage in passages:
                        if joined_passages and re.match(r"\s*(?:다만|이 경우|단,)", passage):
                            joined_passages[-1] += " " + passage
                        else:
                            joined_passages.append(passage)
                    passages = joined_passages
                    title_terms = [term for term in ChunkSearchService.extract_keywords(title)
                                   if not re.fullmatch(r"제?\d+(?:조|항|호).*", term)
                                   and term not in tax_labels
                                   and term not in {"계산", "특례", "범위", "정의", "방법", "규정"}]
                    title_precision = sum(any(term in token for term in weights) for token in title_terms) / max(len(title_terms), 1)
                    title_core = any(term in title for term in core_terms)
                    supported_passages = [passage for passage in passages
                                          if title_core or sum(term in passage for term in core_terms) >= 2]
                    if chunk.law_name not in family or not supported_passages:
                        coverage[chunk.chunk_id] = 0.0
                        term_coverage[chunk.chunk_id] = {term: 0.0 for term in weights}
                        continue
                    # Keep evidence local. Pick one passage, rather than collecting
                    # unrelated matches from every paragraph in a long article.
                    passage = max(supported_passages, key=lambda text: sum(
                        weight for term, weight in weights.items() if term in text
                    ))
                    values = {term: weight / sum(weights.values()) * max(
                        title_precision if term in title else 0.0,
                        1.0 / 3.0 if term in passage else 0.0,
                    ) for term, weight in weights.items()}
                    term_coverage[chunk.chunk_id] = values
                    coverage[chunk.chunk_id] = sum(values.values())
                if any(coverage.values()):
                    requirement_facets.append({"terms": weights, "core_terms": core_terms, "law_names": family})
                    for chunk in ranked:
                        facet_scores.setdefault(chunk.chunk_id, []).append(coverage[chunk.chunk_id])
                        facet_term_scores.setdefault(chunk.chunk_id, []).append(term_coverage[chunk.chunk_id])
            # Distinct, supported requirements are needed; a law name alone cannot
            # reserve a slot. The marginal-utility score contract remains unchanged.
            if len(requirement_facets) > 1:
                base_scores = {chunk.chunk_id: chunk.score or 0.0 for chunk in ranked}
                base_mass = sum(base_scores.values())
                covered = [0.0] * len(requirement_facets)
                covered_terms = [{term: 0.0 for term in facet["terms"]} for facet in requirement_facets]
                remaining = list(ranked)
                selected: list[LawChunk] = []
                while remaining:
                    utilities = {
                        chunk.chunk_id: base_scores[chunk.chunk_id] / base_mass + sum(
                            max(value - covered_terms[index][term], 0.0)
                            for index, values in enumerate(facet_term_scores[chunk.chunk_id])
                            for term, value in values.items()
                        ) for chunk in remaining
                    }
                    best = max(remaining, key=lambda chunk: (utilities[chunk.chunk_id], base_scores[chunk.chunk_id]))
                    before_coverage = list(covered)
                    term_gains = [{term: max(value - covered_terms[index][term], 0.0)
                                   for term, value in values.items()}
                                  for index, values in enumerate(facet_term_scores[best.chunk_id])]
                    covered_terms = [{term: max(value, covered_terms[index][term])
                                      for term, value in values.items()}
                                     for index, values in enumerate(facet_term_scores[best.chunk_id])]
                    covered = [sum(values.values()) for values in covered_terms]
                    best.score = round(utilities[best.chunk_id], 6)
                    selection_trace.append({
                        "chunk_id": best.chunk_id, "rank": len(selected) + 1,
                        "base_score": base_scores[best.chunk_id], "base_prior": base_scores[best.chunk_id] / base_mass,
                        "facet_coverage": facet_scores[best.chunk_id], "term_gains": term_gains,
                        "covered_before": before_coverage,
                        "covered_after": list(covered), "selection_utility": utilities[best.chunk_id],
                    })
                    selected.append(best)
                    remaining.remove(best)
                ranked = selected

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
