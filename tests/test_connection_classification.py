import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.schemas.retrieval import LawChunk
from app.services.chunk_search_service import ChunkSearchService as S


class ConnectionClassificationTest(unittest.TestCase):
    def setUp(self):
        settings = patch("app.core.config.get_settings", return_value=SimpleNamespace(ollama_max_context_chars=3000))
        settings.start()
        self.addCleanup(settings.stop)

    def detail(self, text, question="법인세법 제5조 공제", **kwargs):
        chunk = LawChunk(chunk_id="sample", law_name="법인세법", title="제5조", content=text)
        result = S.build_prompt_context([chunk], return_details=True, question=question,
                                        keywords=["공제", "사업자등록"], **kwargs)
        for assessment in result["diagnostics"]["connection_assessments"]:
            start, end = assessment["source_range"]
            self.assertEqual(text[start:end], assessment["expression"])
            self.assertEqual("unverified", assessment["legal_applicability"])
            self.assertIsNone(assessment["target_range"])
        return result

    def test_structural_qualification_is_not_legal_verification(self):
        result = self.detail("제5조(요건)\n① 공제한다. 다만, 일정 금액은 제외한다.")
        qualification = result["diagnostics"]["connection_assessments"][0]
        self.assertEqual("verified", qualification["structure_status"])
        self.assertIn("structural_connection", qualification["classifications"])
        self.assertEqual(qualification["owner_range"], qualification["structural_owner_range"])
        self.assertEqual("legal_applicability_unverified", qualification["unresolved_reason"])
        self.assertTrue(qualification["selected_failure"])
        self.assertEqual("unresolved", result["identification"])

    def test_explicit_question_condition_is_preserved_without_applicability_claim(self):
        text = "제5조(요건)\n① 공제한다. 다만, 사업자등록을 신청한 경우에는 예외로 한다."
        question = "법인세법 제5조 사업자등록을 신청한 경우를 알려 주세요."
        result = self.detail(text, question)
        qualification = result["diagnostics"]["connection_assessments"][0]
        self.assertIn("question_related", qualification["classifications"])
        self.assertEqual("explicit_text_match", qualification["question_relation"]["status"])
        self.assertTrue(qualification["selected_block_preserved"])
        self.assertFalse(qualification["effective_context_preserved"])
        for evidence in qualification["question_relation"]["evidence"]:
            left, right = evidence["question_range"]
            self.assertEqual(question[left:right], evidence["text"])
        self.assertIsNone(result["context"])

    def test_keyword_overlap_does_not_establish_question_condition(self):
        result = self.detail("제5조(요건)\n① 공제한다. 다만, 사업자등록을 신청한 경우에는 제외한다.",
                             "법인세법 제5조 사업자등록 기준")
        qualification = result["diagnostics"]["connection_assessments"][0]
        self.assertEqual("lexical_overlap", qualification["question_relation"]["status"])
        self.assertNotIn("question_related", qualification["classifications"])

    def test_external_reference_records_only_explicit_target(self):
        result = self.detail("제5조(요건)\n① 공제는 「부가가치세법」 제8조의2제1항제2호에 따른다.")
        reference = result["diagnostics"]["connection_assessments"][0]
        self.assertEqual({"law_name": "부가가치세법", "article": "제8조의2", "units": ["제1항", "제2호"]},
                         reference["reference_target"])
        self.assertEqual("explicit_other_law", reference["reference_scope"])
        self.assertIn("external_reference", reference["classifications"])
        self.assertTrue(all(value == "unverified" for value in reference["target_verification"].values()))
        self.assertEqual("unverified", reference["reference_necessity"])

    def test_reference_without_law_name_does_not_infer_one(self):
        result = self.detail("제5조(요건)\n① 공제는 제5조제2항에 따른다.")
        reference = result["diagnostics"]["connection_assessments"][0]
        self.assertIsNone(reference["reference_target"]["law_name"])
        self.assertEqual("same_article_label", reference["reference_scope"])
        self.assertNotIn("external_reference", reference["classifications"])
        self.assertEqual("unverified", reference["target_verification"]["content"])

    def test_multiple_laws_and_references_remain_separate(self):
        chunks = [LawChunk(chunk_id=identifier, law_name=law, title="제5조", content=text)
                  for identifier, law, text in [
                      ("a", "법인세법", "제5조(요건)\n① 공제는 제8조와 제9조에 따른다."),
                      ("b", "부가가치세법", "제5조(요건)\n① 공제한다. 다만, 일부 금액은 제외한다.")]]
        result = S.build_prompt_context(chunks, return_details=True, keywords=["공제"],
                                        question="법인세법과 부가가치세법 제5조 공제")
        assessments = result["diagnostics"]["connection_assessments"]
        self.assertEqual({"a", "b"}, {a["chunk_id"] for a in assessments if a["selected_block_preserved"]})
        self.assertEqual({"제8조", "제9조"}, {a["reference_target"]["article"] for a in assessments if a["reference_target"]})
        self.assertEqual("unresolved", result["identification"])

    def test_common_introduction_and_qualification_are_both_kept(self):
        result = self.detail("제5조(요건) 공통 도입조건은 다음과 같다.\n1. 공제한다. 다만, 일부는 제외한다.")
        self.assertIn("공통 도입조건", result["blocks"][0]["excerpt"])
        self.assertIn("다만, 일부는 제외한다.", result["blocks"][0]["excerpt"])
        self.assertTrue(all(a["selected_block_preserved"] for a in result["diagnostics"]["connection_assessments"]))

    def test_unknown_structure_is_not_repaired(self):
        result = self.detail("③ 공제한다. 다만, 금액은 제외한다.")
        assessment = result["diagnostics"]["connection_assessments"][0]
        self.assertEqual("unverified", assessment["structure_status"])
        self.assertIn("connection_uncertain", assessment["classifications"])
        self.assertEqual("structural_owner_unverified", assessment["unresolved_reason"])
        self.assertEqual("unresolved", result["identification"])
        self.assertEqual([], result["blocks"])

    def test_headerless_qualification_has_no_invented_owner(self):
        result = self.detail("공제한다. 다만, 금액은 제외한다.")
        assessment = result["diagnostics"]["connection_assessments"][0]
        self.assertIsNone(assessment["structural_owner_range"])
        self.assertFalse(assessment["selected_block_preserved"])
        self.assertFalse(assessment["effective_context_preserved"])

    def test_budget_failure_does_not_claim_effective_preservation(self):
        result = self.detail("제5조(요건)\n① 공제는 제8조에 따른다.", max_context_chars=1)
        reference = result["diagnostics"]["connection_assessments"][0]
        self.assertEqual("exceeded", result["budget"])
        self.assertTrue(reference["selected_block_preserved"])
        self.assertFalse(reference["effective_context_preserved"])

    def test_repeated_references_keep_distinct_source_positions(self):
        result = self.detail("제5조(요건)\n① 공제는 제8조와 제8조에 따른다.")
        references = result["diagnostics"]["connection_assessments"]
        self.assertEqual(2, len(references))
        self.assertEqual(["제8조", "제8조"], [reference["expression"] for reference in references])
        self.assertNotEqual(references[0]["source_range"], references[1]["source_range"])

    def test_trimmed_whitespace_is_not_reported_as_lost_qualification(self):
        text = "제5조(요건)\n① 공제한다. 다만, 일부는 제외한다 \t\n② 다른 사항"
        result = self.detail(text)
        qualification = result["diagnostics"]["connection_assessments"][0]
        source_start, source_end = qualification["source_range"]
        check_start, check_end = qualification["preservation_range"]
        self.assertEqual(text[source_start:source_end].strip(), text[check_start:check_end])
        self.assertLess(check_end, source_end)
        self.assertTrue(qualification["selected_block_preserved"])
        self.assertEqual("unresolved", result["identification"])

    def test_target_candidate_presence_is_not_content_verification(self):
        chunks = [LawChunk(chunk_id="source", law_name="법인세법", title="제5조", content="제5조(요건)\n① 공제는 제8조에 따른다."),
                  LawChunk(chunk_id="target", law_name="법인세법", title="제8조", content="제8조(다른 요건)\n① 공제한다.")]
        result = S.build_prompt_context(chunks, return_details=True, keywords=["공제"], question="법인세법 제5조 공제")
        reference = result["diagnostics"]["connection_assessments"][0]
        self.assertEqual("제8조", reference["reference_target"]["article"])
        self.assertTrue(all(value == "unverified" for value in reference["target_verification"].values()))
        self.assertEqual("unverified", reference["reference_necessity"])
        self.assertEqual("unresolved", result["identification"])

    def test_fixed_cases_separate_selected_preservation_from_effective_context(self):
        root = Path("/Users/songminji/Documents/TaxHelper-benchmarks/runs/20261114T144233285053+0900_common-inputs_af80cf4b9c514d7b8ffdf5b481688a39/inputs")
        if not root.is_dir():
            self.skipTest("고정자료가 없어 실제 문항 분류는 미검증입니다.")
        for qid in ("Q02", "Q03", "Q12", "Q18"):
            with self.subTest(qid=qid):
                record = json.loads((root / qid / "question-and-gold.json").read_text())
                chunks = [S._to_chunk(c) for c in json.loads((root / qid / "sources.json").read_text())["law"]]
                result = S.build_prompt_context(chunks, return_details=True,
                                                search_conditions=S(client=None, candidate_size=30).build_search_conditions(record["question"]))
                expected = {str(g["chunk_id"]) for g in record["gold"]}
                selected = {str(b["chunk_id"]) for b in result["blocks"]}
                self.assertEqual(qid != "Q18", expected <= selected)
                self.assertEqual("unresolved", result["identification"])
                self.assertEqual("within", result["budget"])
                self.assertIsNone(result["context"])
                assessments = result["diagnostics"]["connection_assessments"]
                self.assertFalse(any(a["effective_context_preserved"] for a in assessments))
                if qid != "Q18":
                    retained = [a for a in assessments if str(a["chunk_id"]) in expected and a["selected_block_preserved"]]
                    self.assertTrue(retained)
                    self.assertTrue(all(a["legal_applicability"] == "unverified" for a in retained))
                    targets = {a["reference_target"]["article"] for a in retained if a["reference_target"]}
                    if qid == "Q02":
                        self.assertIn("제60조의2", targets)
                    if qid == "Q03":
                        self.assertIn("제8조", targets)
                        self.assertTrue(any("사업개시일 이전" in a["expression"] for a in retained))
                    if qid == "Q12":
                        self.assertTrue({"제52조", "제18조의4", "제57조"} <= targets)


if __name__ == "__main__":
    unittest.main()
