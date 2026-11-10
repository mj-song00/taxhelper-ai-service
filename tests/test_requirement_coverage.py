import copy
import sys
import unittest
from app.schemas.retrieval import LawChunk
from app.services.chunk_search_service import ChunkSearchService


class RequirementCoverageTest(unittest.TestCase):
    def rank(self, question, chunks, laws, domains=None, top_k=2):
        observed = {}
        def observe(frame, event, result):
            if event == "return" and frame.f_code.co_name == "rank_chunks" and isinstance(result, list):
                observed.update({key: frame.f_locals.get(key) for key in
                                 ("requirement_facets", "selection_trace", "scored")})
        previous = sys.getprofile()
        sys.setprofile(observe)
        try:
            result = ChunkSearchService.rank_chunks(
                copy.deepcopy(chunks), ChunkSearchService.extract_keywords(question), laws,
                question, top_k, explicit_tax_domains=domains)
        finally:
            sys.setprofile(previous)
        return result, observed

    def documents(self):
        return [
            LawChunk(chunk_id="goods", law_name="부가가치세법", title="재화의 공급시기", content="재화가 인도되는 때를 공급시기로 한다."),
            LawChunk(chunk_id="service", law_name="부가가치세법", title="용역의 공급시기", content="용역의 제공이 완료되는 때를 공급시기로 한다."),
            LawChunk(chunk_id="discourse", law_name="부가가치세법", title="안내", content="각각 확인하고 싶습니다. 법에서 알려 주세요."),
            LawChunk(chunk_id="unrelated", law_name="부가가치세법", title="세금계산서", content="공급시기의 일부 규정을 준용한다."),
        ]

    def test_law_particles_and_discourse_do_not_create_coverage(self):
        q = "부가가치세법에서는 재화의 공급시기 기준과 용역의 공급시기 기준을 각각 확인하고 싶습니다."
        result, state = self.rank(q, self.documents(), ["부가가치세법"])
        self.assertEqual({c.chunk_id for c in result}, {"goods", "service"})
        terms = {term for facet in state["requirement_facets"] for term in facet["terms"]}
        self.assertTrue({"재화", "용역"}.issubset(terms))
        self.assertFalse({"법에서", "각각", "알려", "확인"} & terms)

    def test_shared_context_is_retained_in_multi_tax_requirements(self):
        q = "비사업용 지출이 있을 때, 부가가치세법에서는 매입세액 공제 원칙과 법인세법에서는 손금불산입 기준을 각각 알려 주세요."
        chunks = [
            LawChunk(chunk_id="vat", law_name="부가가치세법", title="매입세액 공제", content="비사업용 지출은 매입세액 공제를 하지 않는다."),
            LawChunk(chunk_id="corp", law_name="법인세법", title="손금불산입", content="비사업용 지출을 손금불산입한다."),
            LawChunk(chunk_id="noise", law_name="법인세법", title="안내", content="각각 확인 법에서는 법에서"),
        ]
        result, state = self.rank(q, chunks, ["부가가치세법", "법인세법"], ["부가가치세", "법인세"])
        self.assertEqual({c.chunk_id for c in result}, {"vat", "corp"})
        self.assertEqual(len(state["requirement_facets"]), 2)
        self.assertTrue(all("비사업용" in f["terms"] for f in state["requirement_facets"]))

    def test_single_requirement_preserves_base_scores_and_order(self):
        q = "부가가치세법의 재화 공급시기 기준을 알려 주세요."
        result, state = self.rank(q, self.documents(), ["부가가치세법"])
        self.assertFalse(state["requirement_facets"])
        base = [(chunk.chunk_id, round(score, 4)) for score, chunk in state["scored"]
                if score > 0 and not ChunkSearchService.is_deleted_chunk(chunk)][:2]
        self.assertEqual([(c.chunk_id, c.score) for c in result], base)

    def test_noun_coordination_without_two_predicates_keeps_base_ranking(self):
        q = "부가가치세법에서 재화와 용역의 공급시기 기준을 알려 주세요."
        result, state = self.rank(q, self.documents(), ["부가가치세법"])
        self.assertFalse(state["requirement_facets"])
        base = [(chunk.chunk_id, round(score, 4)) for score, chunk in state["scored"] if score > 0][:2]
        self.assertEqual([(c.chunk_id, c.score) for c in result], base)

    def test_unsupported_second_requirement_does_not_force_a_document(self):
        q = "부가가치세법의 재화 공급시기 기준과 용역 공급시기 기준을 알려 주세요."
        chunks = [c for c in self.documents() if c.chunk_id != "service"]
        result, state = self.rank(q, chunks, ["부가가치세법"])
        self.assertLess(len(state["requirement_facets"]), 2)
        base = [(chunk.chunk_id, round(score, 4)) for score, chunk in state["scored"] if score > 0][:2]
        self.assertEqual([(c.chunk_id, c.score) for c in result], base)

    def test_scattered_body_words_do_not_jointly_satisfy_a_requirement(self):
        q = "부가가치세법에서 재화 인도시기 기준과 용역 완료시기 기준을 알려 주세요."
        chunks = self.documents() + [LawChunk(chunk_id="scattered", law_name="부가가치세법", title="다른 의무", content="재화를 기록한다. 인도시기를 기록한다. 용역을 기록한다. 완료시기를 기록한다.")]
        _, state = self.rank(q, chunks, ["부가가치세법"])
        for row in state["selection_trace"]:
            if row["chunk_id"] == "scattered":
                self.assertEqual(row["facet_coverage"], [0.0, 0.0])

    def test_marginal_utility_contract_and_determinism(self):
        q = "부가가치세법의 재화 공급시기 기준과 용역 공급시기 기준을 알려 주세요."
        a, state = self.rank(q, self.documents(), ["부가가치세법"])
        b, _ = self.rank(q, self.documents(), ["부가가치세법"])
        self.assertEqual([(c.chunk_id, c.score) for c in a], [(c.chunk_id, c.score) for c in b])
        self.assertTrue(state["selection_trace"])
        for row in state["selection_trace"]:
            expected = row["base_prior"] + sum(sum(gains.values()) for gains in row["term_gains"])
            self.assertAlmostEqual(row["selection_utility"], expected)
            self.assertTrue(all(v >= 0 for gains in row["term_gains"] for v in gains.values()))
        self.assertEqual([c.score for c in a], [round(x["selection_utility"], 6) for x in state["selection_trace"][:2]])

    def test_empty_or_insufficient_candidates_keep_fallback(self):
        q = "부가가치세법의 재화 공급시기 기준과 용역 공급시기 기준을 알려 주세요."
        result, state = self.rank(q, self.documents()[:2], ["부가가치세법"], top_k=5)
        self.assertFalse(state["selection_trace"])
        self.assertEqual(len(result), 2)
        result, _ = self.rank(q, [], ["부가가치세법"])
        self.assertEqual(result, [])

    def test_negated_premise_is_not_positive_shared_evidence(self):
        q = "별도 자료제출 대상이 아닌 경우, 부가가치세의 공제 원칙과 법인세의 손금불산입 기준을 각각 알려 주세요."
        chunks = [
            LawChunk(chunk_id="a", law_name="부가가치세법", title="공제", content="공제 원칙을 정한다."),
            LawChunk(chunk_id="b", law_name="법인세법", title="손금불산입", content="손금불산입 기준을 정한다."),
            LawChunk(chunk_id="c", law_name="법인세법", title="자료제출", content="자료제출 자료제출 자료제출"),
        ]
        _, state = self.rank(q, chunks, ["부가가치세법", "법인세법"], ["부가가치세", "법인세"])
        self.assertFalse(any("자료제출" in f["terms"] for f in state["requirement_facets"]))

    def test_attached_proviso_remains_with_its_rule(self):
        q = "부가가치세법의 도서 면세에 광고가 포함되는지 기준과 용역의 공급시기 기준을 알려 주세요."
        chunks = [
            LawChunk(chunk_id="a", law_name="부가가치세법", title="면세", content="도서를 면세한다. 다만, 광고는 제외한다."),
            LawChunk(chunk_id="b", law_name="부가가치세법", title="용역 공급시기", content="용역의 공급시기 기준을 정한다."),
            LawChunk(chunk_id="c", law_name="부가가치세법", title="도서 수입", content="도서를 수입한다."),
        ]
        result, state = self.rank(q, chunks, ["부가가치세법"])
        self.assertTrue(state["selection_trace"])
        trace = next(t for t in state["selection_trace"] if t["chunk_id"] == "a")
        self.assertGreater(trace["facet_coverage"][0], 0)
        self.assertIn("a", [c.chunk_id for c in result])

    def test_separate_exclusion_instruction_is_not_a_requirement(self):
        q = "부가가치세법의 재화 공급시기 기준과 용역 공급시기 기준을 알려 주세요. 별도신고서 제출은 제외합니다."
        _, state = self.rank(q, self.documents(), ["부가가치세법"])
        self.assertFalse(any("별도신고서" in f["terms"] for f in state["requirement_facets"]))
