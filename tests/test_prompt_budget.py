import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.schemas.retrieval import LawChunk
from app.services.chunk_search_service import ChunkSearchService as S


class PromptBudgetTest(unittest.TestCase):
    def setUp(self):
        setting = patch("app.core.config.get_settings", return_value=SimpleNamespace(ollama_max_context_chars=3000))
        setting.start()
        self.addCleanup(setting.stop)

    def chunk(self, identifier="a", law="법인세법", text="제5조(요건)\n① 공제 요건을 충족한다."):
        return LawChunk(chunk_id=identifier, law_name=law, title="제5조 요건", content=text, score=7.0)

    def test_legacy_return_is_unchanged(self):
        chunk = self.chunk()
        self.assertEqual("[1]\n법령명: 법인세법\n조문명: 제5조 요건\n내용:\n제5조(요건) ① 공제 요건을 충족한다.",
                         S.build_prompt_context([chunk]))
        self.assertEqual("검색된 법령 근거가 없습니다.", S.build_prompt_context([]))

    def test_explicit_article_and_source_ranges(self):
        chunk = self.chunk()
        before = copy.deepcopy(chunk)
        result = S.build_prompt_context([chunk], keywords=["공제"], question="법인세법 제5조 공제",
                                        return_details=True)
        self.assertEqual("rule_selected", result["identification"])
        self.assertEqual("within", result["budget"])
        self.assertEqual(len(result["context"]), result["char_count"])
        self.assertEqual(before, chunk)
        block = result["blocks"][0]
        self.assertEqual(" ... ".join(" ".join(chunk.content[left:right].split())
                                     for left, right in block["ranges"]), block["excerpt"])

    def test_keywords_alone_are_not_success(self):
        result = S.build_prompt_context([self.chunk()], keywords=["공제"], return_details=True)
        self.assertEqual("unverified", result["identification"])
        self.assertIsNone(result["context"])

    def test_existing_concept_role_can_select_without_article(self):
        conditions = {"keywords": ["공제"], "required_roles": ["LEGAL_EFFECT"],
                      "concepts": [{"role": "LEGAL_EFFECT", "search_terms": ["공제"], "weight": 5}]}
        result = S.build_prompt_context([self.chunk()], search_conditions=conditions, return_details=True)
        self.assertEqual("rule_selected", result["identification"])

    def test_two_laws_are_reserved_before_optional_evidence(self):
        chunks = [self.chunk(), self.chunk("b", "부가가치세법")]
        result = S.build_prompt_context(chunks, keywords=["공제"],
                                        question="법인세법과 부가가치세법 제5조 공제", return_details=True)
        self.assertEqual("rule_selected", result["identification"])
        self.assertEqual({"법인세법", "부가가치세법"}, {b["law_name"] for b in result["blocks"] if b["required"]})

    def test_missing_required_law_is_unresolved(self):
        result = S.build_prompt_context([self.chunk()], keywords=["공제"],
                                        question="법인세법과 부가가치세법 제5조 공제", return_details=True)
        self.assertEqual("unresolved", result["identification"])
        self.assertIsNone(result["context"])

    def test_qualification_and_reference_are_not_silently_resolved(self):
        for suffix in [" 다만, 해당자는 제외한다.", " 제6조에 따른다."]:
            with self.subTest(suffix=suffix):
                result = S.build_prompt_context([self.chunk(text="제5조(요건)\n① 공제한다." + suffix)],
                                                keywords=["공제"], question="법인세법 제5조 공제", return_details=True)
                self.assertEqual("unresolved", result["identification"])
                self.assertIsNone(result["context"])
                self.assertIn(suffix.strip(), result["blocks"][0]["excerpt"])

    def test_required_budget_overflow_does_not_cut(self):
        result = S.build_prompt_context([self.chunk()], keywords=["공제"], question="법인세법 제5조 공제",
                                        max_context_chars=1, return_details=True)
        self.assertEqual("exceeded", result["budget"])
        self.assertIsNone(result["context"])
        self.assertIn("공제 요건을 충족한다.", result["blocks"][0]["excerpt"])

    def test_exact_3000_includes_reservations_and_outer_text(self):
        kwargs = dict(keywords=["공제"], question="법인세법 제5조 공제", return_details=True)
        base = S.build_prompt_context([self.chunk()], **kwargs)
        reserved = "\n[판례·보충 근거]\n" + "가" * (3000 - base["char_count"] - len("\n[판례·보충 근거]\n"))
        exact = S.build_prompt_context([self.chunk()], reserved_context=reserved, **kwargs)
        self.assertEqual(3000, exact["char_count"])
        self.assertTrue(exact["context"].endswith(reserved))
        overflow = S.build_prompt_context([self.chunk()], reserved_context=reserved + "가", **kwargs)
        self.assertEqual("exceeded", overflow["budget"])

    def test_setting_limit_is_not_bypassed(self):
        with patch("app.core.config.get_settings", return_value=SimpleNamespace(ollama_max_context_chars=20)):
            result = S.build_prompt_context([self.chunk()], keywords=["공제"], question="법인세법 제5조 공제",
                                            max_context_chars=10000, return_details=True)
        self.assertEqual(20, result["char_limit"])
        self.assertEqual("exceeded", result["budget"])

    def test_branched_article_does_not_match_base_article(self):
        chunk = self.chunk(text="제5조의2(요건)\n① 공제 요건이다.")
        result = S.build_prompt_context([chunk], keywords=["공제"], question="법인세법 제5조 공제", return_details=True)
        self.assertEqual("unresolved", result["identification"])

    def test_common_introduction_is_preserved_with_item(self):
        chunk = self.chunk(text="제5조(요건)\n① 공통 조건\n1. 공제 요건이다.")
        result = S.build_prompt_context([chunk], keywords=["공제"], question="법인세법 제5조 공제",
                                        return_details=True)
        self.assertEqual("rule_selected", result["identification"])
        self.assertIn("공통 조건", result["context"])
        self.assertIn("1. 공제 요건이다.", result["context"])

    def test_missing_role_is_not_reported_as_success(self):
        result = S.build_prompt_context([self.chunk()], keywords=["공제"],
                                        question="법인세법 제5조 공제", return_details=True,
                                        search_conditions={"required_roles": ["DUTY"], "concepts": []})
        self.assertEqual("unresolved", result["identification"])
        self.assertIsNone(result["context"])

    def test_optional_units_do_not_displace_required_evidence(self):
        chunks = [self.chunk(), self.chunk("optional", text="제5조(요건)\n① 공제 " + "가" * 3100)]
        result = S.build_prompt_context(chunks, keywords=["공제"], question="법인세법 제5조 공제",
                                        return_details=True)
        self.assertEqual("within", result["budget"])
        self.assertEqual(["a"], [block["chunk_id"] for block in result["blocks"]])

    def test_empty_candidates_are_not_budget_evaluated(self):
        result = S.build_prompt_context([], return_details=True)
        self.assertEqual("unresolved", result["identification"])
        self.assertEqual("not_evaluated", result["budget"])

    def test_fixed_cases_report_selection_separately_from_preservation(self):
        root = Path("/Users/songminji/Documents/TaxHelper-benchmarks/runs/20261114T144233285053+0900_common-inputs_af80cf4b9c514d7b8ffdf5b481688a39/inputs")
        if not root.is_dir():
            self.skipTest("고정 평가자료가 없어 선정·보존은 미검증입니다.")
        for qid in ("Q02", "Q03", "Q18", "Q12"):
            with self.subTest(qid=qid):
                question = json.loads((root / qid / "question-and-gold.json").read_text())
                sources = json.loads((root / qid / "sources.json").read_text())
                chunks = [S._to_chunk(chunk) for chunk in sources["law"]]
                conditions = S(client=None, candidate_size=30).build_search_conditions(question["question"])
                result = S.build_prompt_context(chunks, search_conditions=conditions, return_details=True)
                expected_ids = {str(gold["chunk_id"]) for gold in question["gold"]}
                selected_ids = {str(block["chunk_id"]) for block in result["blocks"]}
                selected = expected_ids <= selected_ids
                # 후보 ID 포함과 원문 단위 보존, 유효 입력 수용은 별개로 관측한다.
                selected_text_preserved = selected and all(block["excerpt"] for block in result["blocks"])
                preserved = result["context"] is not None and selected and all(
                    block["excerpt"] in result["context"] for block in result["blocks"])
                print(f"{qid}: 기대 근거 후보선정={selected}, 유효 context 보존={preserved}, "
                      f"선정 블록 원문보존={selected_text_preserved}, 식별={result['identification']}, 예산={result['budget']}")
                if result["context"] is not None:
                    self.assertEqual("rule_selected", result["identification"])
                    self.assertEqual(len(result["context"]), result["char_count"])
                    self.assertLessEqual(result["char_count"], 3000)
                for block in result["blocks"]:
                    original = next(chunk.content for chunk in chunks if chunk.chunk_id == block["chunk_id"])
                    self.assertEqual(block["excerpt"], " ... ".join(" ".join(original[a:b].split())
                                                                  for a, b in block["ranges"]))

    def test_diagnostics_explicit_filters_record_actual_stage(self):
        for chunk, reason in [(self.chunk(law="부가가치세법"), "explicit_law_mismatch"),
                              (self.chunk(text="제6조(요건)\n① 공제한다."), "explicit_article_mismatch")]:
            with self.subTest(reason=reason):
                result = S.build_prompt_context([chunk], keywords=["공제"],
                                                question="법인세법 제5조 공제", return_details=True)
                diagnostic = next(d for d in result["diagnostics"]["candidates"] if d.get("unit_label") == "①")
                self.assertEqual("excluded", diagnostic["decision"])
                self.assertEqual({"stage": "explicit_filter", "reason": reason}, diagnostic["decision_trace"][-1])
                self.assertEqual(reason != "explicit_law_mismatch", diagnostic["explicit_law_match"])
                self.assertEqual(reason != "explicit_article_mismatch", diagnostic["explicit_article_match"])

    def test_diagnostics_concepts_roles_weights_reuse_existing_values(self):
        conditions = {"keywords": ["공제"], "keyword_weights": {"공제": 7.5}, "required_roles": ["LEGAL_EFFECT"],
                      "concepts": [{"concept_id": "probe", "role": "LEGAL_EFFECT", "search_terms": ["공제"]}]}
        result = S.build_prompt_context([self.chunk()], search_conditions=conditions, return_details=True)
        candidate = next(d for d in result["diagnostics"]["candidates"] if d.get("unit_label") == "①")
        self.assertEqual([{"term": "공제", "weight": 7.5}], candidate["keyword_matches"])
        self.assertEqual(["LEGAL_EFFECT"], candidate["matched_roles"])
        self.assertEqual("probe", candidate["concept_matches"][0]["concept_id"])
        self.assertEqual(7.5, candidate["priority"][2])
        self.assertEqual("selected_required", candidate["decision"])

    def test_diagnostics_boundary_and_parent_are_distinct(self):
        chunk = self.chunk(text="① 공제\n가. 상위 호 없는 목\n설명 2. 줄 중간 번호")
        result = S.build_prompt_context([chunk], keywords=["공제"], return_details=True)
        candidates = result["diagnostics"]["candidates"]
        subitem = next(d for d in candidates if d.get("unit_type") == "subitem")
        inline = next(d for d in candidates if d.get("unit_label") == "2.")
        self.assertIn("parent_unverified", {f["code"] for f in subitem["structure_findings"]})
        self.assertIn("boundary_unverified", {f["code"] for f in inline["structure_findings"]})
        orphan = S.build_prompt_context([self.chunk(text="1. 공제")], keywords=["공제"], return_details=True)
        item = orphan["diagnostics"]["candidates"][0]
        self.assertIn("parent_unverified", {f["code"] for f in item["structure_findings"]})

    def test_diagnostics_numbering_gap_duplicate_and_order(self):
        chunk = self.chunk(text="① 공제\n1. 첫 호\n3. 누락\n2. 역순\n2. 중복")
        result = S.build_prompt_context([chunk], keywords=["공제"], return_details=True)
        findings = [finding for d in result["diagnostics"]["candidates"] for finding in d["structure_findings"]]
        codes = {finding["code"] for finding in findings}
        self.assertTrue({"numbering_gap", "numbering_order", "numbering_duplicate", "numbering_group_unverified"} <= codes)
        first = next(d for d in result["diagnostics"]["candidates"] if d.get("unit_label") == "1.")
        self.assertEqual("excluded", first["decision"])
        self.assertIn("numbering_group_unverified", {f["code"] for f in first["structure_findings"]})

    def test_diagnostics_qualification_reference_and_multiple_failures(self):
        text = "제5조(요건)\n① 공제한다. 다만, 제8조제1항에 따른 금액은 제외한다."
        result = S.build_prompt_context([self.chunk(text=text)], keywords=["공제"],
                                        question="법인세법 제5조 공제", return_details=True)
        failures = result["diagnostics"]["failures"]
        self.assertTrue({"qualification_target_unverified", "reference_relation_unverified"}
                        <= {f["condition"] for f in failures})
        self.assertEqual("connection_check", result["diagnostics"]["first_failure"]["stage"])
        for failure in failures:
            evidence = failure["evidence"]
            left, right = evidence["source_range"]
            self.assertEqual(text[left:right], evidence["expression"])
            self.assertIsNone(evidence["target_range"])
        self.assertIsNotNone(next(f["evidence"]["owner_range"] for f in failures
                                 if f["condition"] == "qualification_target_unverified"))

    def test_diagnostics_introduction_is_structural_not_legal(self):
        text = "제5조(요건) 공통 도입\n1. 공제한다."
        result = S.build_prompt_context([self.chunk(text=text)], keywords=["공제"],
                                        question="법인세법 제5조 공제", return_details=True)
        candidate = next(d for d in result["diagnostics"]["candidates"] if d.get("unit_label") == "1.")
        self.assertEqual("structurally_verified", candidate["introduction_status"])
        self.assertFalse(any(f["code"] == "introduction_unverified" for f in candidate["connection_findings"]))

    def test_diagnostics_reservation_competition_does_not_change_choice(self):
        result = S.build_prompt_context([self.chunk(), self.chunk("second")], keywords=["공제"], return_details=True)
        candidates = [d for d in result["diagnostics"]["candidates"] if d.get("unit_label") == "①"]
        first, second = candidates
        self.assertEqual("selected_required", first["decision"])
        self.assertEqual("not_reserved", second["decision"])
        reservation = next(event for event in second["decision_trace"] if event["stage"] == "law_reservation")
        self.assertEqual("a", reservation["reserved_candidates"][0]["chunk_id"])
        self.assertEqual("rule_not_satisfied", second["decision_trace"][-1]["reason"])
        self.assertEqual(["a"], [b["chunk_id"] for b in result["blocks"]])

    def test_diagnostics_budget_failure_is_not_identification_failure(self):
        result = S.build_prompt_context([self.chunk()], keywords=["공제"], question="법인세법 제5조 공제",
                                        max_context_chars=1, return_details=True)
        self.assertEqual("rule_selected", result["identification"])
        diagnostic = result["diagnostics"]
        self.assertTrue(diagnostic["budget_exceeded"])
        self.assertEqual("budget", diagnostic["first_failure"]["stage"])
        self.assertEqual("a", diagnostic["selected_candidates"][0]["chunk_id"])

    def test_diagnostics_fixed_cases_preserve_unchanged_snapshots_and_q02_change(self):
        root = Path("/Users/songminji/Documents/TaxHelper-benchmarks/runs/20261114T144233285053+0900_common-inputs_af80cf4b9c514d7b8ffdf5b481688a39/inputs")
        if not root.is_dir():
            self.skipTest("고정 평가자료가 없어 진단은 미검증입니다.")
        # Q02는 승인된 목적 구분 변경 후 지문, 나머지는 진단 추가 전 지문이다.
        fingerprints = {"Q02": "f1c79cfc59873aa61f45c40e37e7066342be3f4405baf27b70df0b803faf3264",
                        "Q03": "77aab22786cfd616398f04816b769bf4ffb3ca13d0b9bbf1d6b959eac45cc909",
                        "Q12": "f6c8b760658a2c66f68adbc142ab10734218f06dacc833e7407ff17bf0c751a5",
                        "Q18": "67c17ede7c411c91d5b646dd54dbd01f4f37db1104137e37275cb3fca4a80806"}
        for qid, fingerprint in fingerprints.items():
            with self.subTest(qid=qid):
                record = json.loads((root / qid / "question-and-gold.json").read_text())
                chunks = [S._to_chunk(c) for c in json.loads((root / qid / "sources.json").read_text())["law"]]
                conditions = S(client=None, candidate_size=30).build_search_conditions(record["question"])
                result = S.build_prompt_context(chunks, return_details=True, search_conditions=conditions)
                diagnostics = result.pop("diagnostics")
                self.assertEqual("a3b-v1", result["input_version"])
                # 입력 구성 버전 변경을 제외한 나머지 결과를 비교한다.
                result["input_version"] = "a3-v1"
                snapshot = {"default": S.build_prompt_context(chunks), "detail": result}
                self.assertEqual(fingerprint, hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest())
                self.assertEqual("unresolved", result["identification"])
                self.assertFalse(diagnostics["budget_exceeded"])
                expected = {str(g["chunk_id"]) for g in record["gold"]}
                candidates = [d for d in diagnostics["candidates"] if str(d["chunk_id"]) in expected]
                self.assertTrue(candidates)
                if qid == "Q02":
                    chosen = next(d for d in candidates if d.get("unit_label") == "①")
                    self.assertEqual("selected_required", chosen["decision"])
                    self.assertEqual("matched", chosen["purpose"]["status"])
                    self.assertEqual(expected, {str(b["chunk_id"]) for b in result["blocks"]})
                    first = next(d for d in diagnostics["candidates"]
                                 if d.get("unit_label") == "①" and d["search_rank"] == 1)
                    self.assertEqual("condition_conflict", first["purpose"]["status"])
                    self.assertEqual(chosen["legacy_priority"][:3], first["legacy_priority"][:3])
                if qid == "Q18":
                    codes = {f["code"] for d in candidates for f in d.get("structure_findings", [])}
                    self.assertTrue({"numbering_gap", "numbering_order", "numbering_unsupported", "numbering_group_unverified"} <= codes)
                    self.assertNotIn("numbering_duplicate", codes)
                if qid in {"Q03", "Q12"}:
                    codes = {f["condition"] for f in diagnostics["failures"]}
                    self.assertTrue({"qualification_target_unverified", "reference_relation_unverified"} <= codes)
                    references = [f["evidence"]["expression"] for f in diagnostics["failures"]
                                  if f["condition"] == "reference_relation_unverified"]
                    if qid == "Q03":
                        self.assertTrue(any("제8조" in expression for expression in references))

    def test_diagnostics_early_failure_preserves_contract(self):
        result = S.build_prompt_context([], return_details=True)
        self.assertEqual("not_evaluated", result["budget"])
        self.assertIsNone(result["diagnostics"]["budget_exceeded"])
        self.assertEqual("no_selected_candidate", result["diagnostics"]["first_failure"]["condition"])
        self.assertEqual([], result["diagnostics"]["selected_candidates"])

    def test_purpose_filing_is_prioritized_over_submission(self):
        chunks = [self.chunk("submission", text="제5조(제출)\n① 명세서를 3개월 이내에 제출하여야 한다."),
                  self.chunk("filing", text="제6조(신고)\n① 과세표준을 3개월 이내에 신고하여야 한다.")]
        before = copy.deepcopy(chunks)
        result = S.build_prompt_context(chunks, keywords=["3개월"], question="과세표준을 언제까지 신고해야 하나요?",
                                        return_details=True)
        self.assertEqual("filing", result["blocks"][0]["chunk_id"])
        self.assertEqual(before, chunks)
        self.assertEqual(2, result["blocks"][0]["search_rank"])
        filing = next(c for c in result["diagnostics"]["candidates"]
                      if c.get("unit_label") == "①" and c["chunk_id"] == "filing")
        self.assertEqual("matched", filing["purpose"]["status"])

    def test_purpose_submission_is_not_collapsed_into_filing(self):
        chunks = [self.chunk("filing", text="제5조(신고)\n① 20일 이내에 신고하여야 한다."),
                  self.chunk("submission", text="제6조(제출)\n① 20일 이내에 제출하여야 한다.")]
        result = S.build_prompt_context(chunks, keywords=["20일"], question="서류를 언제까지 제출해야 하나요?",
                                        return_details=True)
        self.assertEqual("submission", result["blocks"][0]["chunk_id"])
        self.assertEqual(["제출"], result["diagnostics"]["question_purpose"]["actions"])

    def test_purpose_excluded_objects_are_general_not_question_specific(self):
        for noun in ("명세서", "확인서", "신청서"):
            with self.subTest(noun=noun):
                chunks = [self.chunk("excluded", text=f"제5조({noun} 제출)\n① {noun}를 제출하여야 한다."),
                          self.chunk("main", text="제6조(신고)\n① 과세표준을 신고하여야 한다.")]
                question = f"{noun}를 제출하는 경우가 아닌 사업자는 과세표준을 신고해야 하나요?"
                result = S.build_prompt_context(chunks, keywords=[noun, "과세표준"], question=question,
                                                return_details=True)
                self.assertEqual("main", result["blocks"][0]["chunk_id"])
                excluded = next(c for c in result["diagnostics"]["candidates"]
                                if c.get("unit_label") == "①" and c["chunk_id"] == "excluded")
                self.assertEqual("condition_conflict", excluded["purpose"]["status"])
                condition = excluded["purpose"]["condition_conflicts"][0]
                start, end = condition["question_range"]
                self.assertIn(noun, question[start:end])
                self.assertEqual("unverified", excluded["purpose"]["legal_applicability"])

    def test_purpose_ambiguous_requests_remain_unresolved(self):
        result = S.build_prompt_context([self.chunk(text="제5조(신고)\n① 20일 이내에 신고하여야 한다.")],
                                        keywords=["20일"], question="신고기한과 제출기한을 알려 주세요.", return_details=True)
        self.assertEqual("unresolved", result["identification"])
        self.assertIsNone(result["context"])
        self.assertEqual(["신고", "제출"], result["diagnostics"]["question_purpose"]["actions"])

    def test_purpose_unrecognized_exclusion_is_not_inferred(self):
        result = S.build_prompt_context([self.chunk(text="제5조(신고)\n① 신고하여야 한다.")], keywords=["신고"],
                                        question="제출 대상이 아닌 사람은 신고해야 하나요?", return_details=True)
        self.assertTrue(result["diagnostics"]["question_purpose"]["condition_unverified"])
        self.assertEqual("unresolved", result["identification"])

    def test_purpose_parenthetical_action_is_not_main_action(self):
        text = "제5조(신고)\n① 과세표준을 20일(확인서를 제출하여야 한다는 경우에는 30일) 이내에 신고하여야 한다."
        result = S.build_prompt_context([self.chunk(text=text)], keywords=["과세표준"],
                                        question="과세표준을 언제까지 신고해야 하나요?", return_details=True)
        purpose = next(c["purpose"] for c in result["diagnostics"]["candidates"] if c.get("unit_label") == "①")
        self.assertEqual(["신고"], purpose["actions"])
        self.assertEqual("matched", purpose["status"])
        for start, end in purpose["action_ranges"]:
            self.assertEqual("신고하여야 한다", text[start:end])

    def test_purpose_deadline_requires_same_clause_time_expression(self):
        chunks = [self.chunk("unknown", text="제5조(신고)\n① 신고하여야 한다."),
                  self.chunk("deadline", text="제6조(신고)\n① 20일 이내에 신고하여야 한다.")]
        result = S.build_prompt_context(chunks, keywords=["신고"], question="언제까지 신고해야 하나요?",
                                        return_details=True)
        self.assertEqual("deadline", result["blocks"][0]["chunk_id"])
        unknown = next(c for c in result["diagnostics"]["candidates"]
                       if c.get("unit_label") == "①" and c["chunk_id"] == "unknown")
        self.assertEqual("unresolved", unknown["purpose"]["status"])

    def test_purpose_only_mismatched_candidate_is_not_success(self):
        chunk = self.chunk(text="제5조(제출)\n① 20일 이내에 제출하여야 한다.")
        result = S.build_prompt_context([chunk], keywords=["20일"],
                                        question="법인세법 제5조에서는 언제까지 신고해야 하나요?", return_details=True)
        self.assertEqual("unresolved", result["identification"])
        self.assertIsNone(result["context"])

    def test_purpose_unbalanced_scope_is_not_confirmed(self):
        chunk = self.chunk(text="제5조(신고)\n① 20일(특례 이내에 신고하여야 한다.")
        result = S.build_prompt_context([chunk], keywords=["20일"],
                                        question="법인세법 제5조에 따라 언제까지 신고해야 하나요?", return_details=True)
        purpose = next(c["purpose"] for c in result["diagnostics"]["candidates"] if c.get("unit_label") == "①")
        self.assertFalse(purpose["scope_balanced"])
        self.assertEqual("unresolved", result["identification"])

    def test_purpose_tie_keeps_search_order_and_default_string(self):
        chunks = [self.chunk("first", text="제5조(신고)\n① 20일 이내에 신고하여야 한다."),
                  self.chunk("second", text="제6조(신고)\n① 20일 이내에 신고하여야 한다.")]
        old = S.build_prompt_context(chunks, keywords=["20일"])
        result = S.build_prompt_context(chunks, keywords=["20일"], question="언제까지 신고해야 하나요?",
                                        return_details=True)
        self.assertEqual("first", result["blocks"][0]["chunk_id"])
        self.assertEqual(old, S.build_prompt_context(chunks, keywords=["20일"]))

    def test_purpose_quoted_duty_is_not_candidate_main_action(self):
        chunk = self.chunk(text='제5조(절차)\n① "신고하여야 한다"는 표현을 확인한다.')
        result = S.build_prompt_context([chunk], keywords=["표현"],
                                        question="법인세법 제5조에서 신고해야 하나요?", return_details=True)
        purpose = next(c["purpose"] for c in result["diagnostics"]["candidates"] if c.get("unit_label") == "①")
        self.assertEqual([], purpose["actions"])
        self.assertEqual("unresolved", result["identification"])

    def test_purpose_relative_clause_is_not_direct_duty(self):
        chunk = self.chunk(text="제5조(절차)\n① 신고하여야 한다는 규정의 표현을 확인한다.")
        result = S.build_prompt_context([chunk], keywords=["표현"],
                                        question="법인세법 제5조에서 신고해야 하나요?", return_details=True)
        purpose = next(c["purpose"] for c in result["diagnostics"]["candidates"] if c.get("unit_label") == "①")
        self.assertEqual([], purpose["actions"])
        self.assertEqual("unresolved", result["identification"])


if __name__ == "__main__":
    unittest.main()
