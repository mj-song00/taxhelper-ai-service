from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LegalConceptSpec:
    concept_id: str
    role: str
    aliases: tuple[str, ...]
    search_terms: tuple[str, ...]
    weight: float
    law_hints: tuple[str, ...] = ()


# A concept is intentionally broader than a question-specific synonym list.
# `aliases` are expressions users commonly type, while `search_terms` are
# expressions that tend to appear in statutes and judgments.
LEGAL_CONCEPT_SPECS: tuple[LegalConceptSpec, ...] = (
    LegalConceptSpec(
        concept_id="LOCAL_TAX",
        role="DOMAIN",
        aliases=("지방세", "취득세", "재산세", "등록면허세"),
        search_terms=("지방세", "취득세", "재산세"),
        weight=4.0,
        law_hints=(
            "지방세법",
            "지방세법 시행령",
            "지방세특례제한법",
            "지방세특례제한법 시행령",
        ),
    ),
    LegalConceptSpec(
        concept_id="TAX_RELIEF",
        role="BENEFIT",
        aliases=("감면", "세액감면", "면제", "경감", "감면받"),
        search_terms=("감면", "면제", "경감", "감면받은 세액"),
        weight=2.0,
    ),
    LegalConceptSpec(
        concept_id="REAL_ESTATE",
        role="OBJECT",
        aliases=("부동산", "토지", "건축물", "주택"),
        search_terms=("부동산", "토지", "건축물"),
        weight=2.5,
    ),
    LegalConceptSpec(
        concept_id="GRACE_PERIOD",
        role="TIME_CONDITION",
        aliases=("유예기간", "유예 기간", "기간 내", "기한 내"),
        search_terms=(
            "유예기간",
            "유예기간 내",
            "취득일부터 1년이 경과할 때까지",
            "취득일부터",
            "일정 기간 내",
            "기간이 경과할 때까지",
        ),
        weight=7.0,
    ),
    LegalConceptSpec(
        concept_id="DIRECT_USE",
        role="DUTY",
        aliases=("직접 사용", "직접사용", "해당 용도에 사용"),
        search_terms=("직접 사용", "해당 용도에 직접 사용", "직접 사용하지 아니"),
        weight=8.0,
    ),
    LegalConceptSpec(
        concept_id="OTHER_PURPOSE_USE",
        role="BREACH",
        aliases=(
            "다른 용도로 사용",
            "다른 용도 사용",
            "용도 변경",
            "용도변경",
            "목적 외 사용",
            "다른 용도로",
        ),
        search_terms=(
            "다른 용도로 사용하는 경우",
            "다른 용도로 사용",
            "용도변경",
            "목적 외 사용",
        ),
        weight=9.0,
    ),
    LegalConceptSpec(
        concept_id="CLAWBACK",
        role="LEGAL_EFFECT",
        aliases=("추징", "추징하다", "감면세액 추징", "감면세액을 징수"),
        search_terms=("추징", "감면된 취득세", "감면받은 세액을 추징", "징수한다"),
        weight=10.0,
    ),
    LegalConceptSpec(
        concept_id="JUST_CAUSE",
        role="EXCEPTION",
        aliases=("정당한 사유", "정당한 이유", "불가피한 사유"),
        search_terms=("정당한 사유 없이", "정당한 사유", "정당한 이유"),
        weight=8.0,
    ),
    LegalConceptSpec(
        concept_id="SANCTION",
        role="LEGAL_EFFECT",
        aliases=("가산세", "제재", "불이익", "처벌"),
        search_terms=("가산세", "제재", "불이익", "처벌"),
        weight=7.0,
    ),
    LegalConceptSpec(
        concept_id="FILING_DEADLINE",
        role="TIME_CONDITION",
        aliases=("신고기한", "신고 기한", "납부기한", "납부 기한"),
        search_terms=("신고기한", "납부기한", "신고하여야", "납부하여야"),
        weight=7.0,
    ),
)


CONCEPT_BY_ID = {spec.concept_id: spec for spec in LEGAL_CONCEPT_SPECS}


# These rules infer expressions used in statutes from a user's fact pattern.
# They describe reusable legal frames rather than individual questions.
CONCEPT_INFERENCE_RULES: tuple[tuple[frozenset[str], tuple[str, ...]], ...] = (
    (
        frozenset({"TAX_RELIEF", "REAL_ESTATE", "OTHER_PURPOSE_USE"}),
        ("DIRECT_USE", "JUST_CAUSE", "CLAWBACK"),
    ),
    (
        frozenset({"TAX_RELIEF", "GRACE_PERIOD", "CLAWBACK"}),
        ("DIRECT_USE", "JUST_CAUSE"),
    ),
)


def normalize_spaces(value: str) -> str:
    return " ".join(value.lower().split())


def extract_legal_concepts(question: str) -> list[dict]:
    normalized_question = normalize_spaces(question)
    compact_question = normalized_question.replace(" ", "")
    found: dict[str, dict] = {}

    for spec in LEGAL_CONCEPT_SPECS:
        matched_alias = next(
            (
                alias
                for alias in sorted(spec.aliases, key=len, reverse=True)
                if normalize_spaces(alias) in normalized_question
                or normalize_spaces(alias).replace(" ", "") in compact_question
            ),
            None,
        )
        if matched_alias:
            found[spec.concept_id] = {
                "concept_id": spec.concept_id,
                "role": spec.role,
                "surface": matched_alias,
                "weight": spec.weight,
                "search_terms": list(spec.search_terms),
                "law_hints": list(spec.law_hints),
                "inferred": False,
            }

    # `정당한가`, `정당한지` expresses a validity question, not necessarily
    # the statutory exception "정당한 사유". The exception is inferred only
    # when a relief/usage frame is already present by the rules below.
    changed = True
    while changed:
        changed = False
        concept_ids = frozenset(found)
        for required_ids, inferred_ids in CONCEPT_INFERENCE_RULES:
            if not required_ids.issubset(concept_ids):
                continue
            for concept_id in inferred_ids:
                if concept_id in found:
                    continue
                spec = CONCEPT_BY_ID[concept_id]
                found[concept_id] = {
                    "concept_id": spec.concept_id,
                    "role": spec.role,
                    "surface": None,
                    "weight": spec.weight * 0.9,
                    "search_terms": list(spec.search_terms),
                    "law_hints": list(spec.law_hints),
                    "inferred": True,
                }
                changed = True

    return sorted(
        found.values(),
        key=lambda item: (item["weight"], not item["inferred"]),
        reverse=True,
    )


def build_weighted_terms(concepts: list[dict]) -> dict[str, float]:
    weighted_terms: dict[str, float] = {}
    for concept in concepts:
        weight = float(concept["weight"])
        for index, term in enumerate(concept["search_terms"]):
            term_weight = weight if index == 0 else weight * 0.9
            weighted_terms[term] = max(weighted_terms.get(term, 0.0), term_weight)
    return weighted_terms


def infer_required_roles(concepts: list[dict]) -> list[str]:
    explicit_roles = {
        concept["role"]
        for concept in concepts
        if not concept["inferred"]
    }
    required = {
        role
        for role in explicit_roles
        if role in {"TIME_CONDITION", "DUTY", "BREACH", "LEGAL_EFFECT"}
    }
    if "LEGAL_EFFECT" in explicit_roles and "BENEFIT" in explicit_roles:
        required.add("BENEFIT")
    return sorted(required)
