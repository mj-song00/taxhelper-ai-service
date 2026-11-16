import copy
import unittest
from app.schemas.retrieval import LawChunk
from app.services.chunk_search_service import ChunkSearchService


class MultiEvidenceRerankTest(unittest.TestCase):
    def setUp(self):
        self.question = "부가가치세 등록번호 확인 원칙과 법인세 손금불산입 요건을 각각 알려 주세요"
        self.keywords = ["부가가치세", "등록번호", "법인세", "손금불산입", "공제"]
        self.laws = ["부가가치세법", "법인세법"]
        self.chunks = [
            LawChunk(chunk_id="invoice", law_name="부가가치세법", title="등록번호 확인", content="공급받는 자의 등록번호를 확인한다."),
            LawChunk(chunk_id="near-duplicate", law_name="부가가치세법", title="등록번호 확인 공제", content="등록번호를 확인하여 공제 공제 공제"),
            LawChunk(chunk_id="expense", law_name="법인세법", title="손금불산입 요건", content="손금불산입 요건을 정한다."),
            LawChunk(chunk_id="wrong-corp", law_name="법인세법", title="법인세 공제", content="법인세 공제 공제 공제"),
            LawChunk(chunk_id="noise", law_name="부가가치세법", title="서류", content="서류를 보관한다."),
        ]

    def test_requirements_are_covered_without_duplicate_dominance(self):
        result = ChunkSearchService.rank_chunks(copy.deepcopy(self.chunks), self.keywords, self.laws, self.question, 2, explicit_tax_domains=["부가가치세", "법인세"])
        ids = {c.chunk_id for c in result}
        self.assertIn("expense", ids)
        self.assertTrue(ids & {"invoice", "near-duplicate"})
        self.assertNotIn("wrong-corp", ids)

    def test_law_name_alone_does_not_force_a_slot(self):
        chunks = [c for c in self.chunks if c.chunk_id != "expense"]
        before = ChunkSearchService.rank_chunks(copy.deepcopy(chunks), self.keywords, self.laws, self.question, 2)
        after = ChunkSearchService.rank_chunks(copy.deepcopy(chunks), self.keywords, self.laws, self.question, 2, explicit_tax_domains=["부가가치세", "법인세"])
        self.assertEqual([(c.chunk_id,c.score) for c in before], [(c.chunk_id,c.score) for c in after])

    def test_single_requirement_keeps_existing_scores_and_order(self):
        before = ChunkSearchService.rank_chunks(copy.deepcopy(self.chunks), self.keywords, self.laws, self.question, 2)
        after = ChunkSearchService.rank_chunks(copy.deepcopy(self.chunks), self.keywords, self.laws, self.question, 2, explicit_tax_domains=["부가가치세"])
        self.assertEqual([(c.chunk_id,c.score) for c in before], [(c.chunk_id,c.score) for c in after])

    def test_insufficient_candidates_keep_existing_behavior(self):
        chunks = self.chunks[:2]
        before = ChunkSearchService.rank_chunks(copy.deepcopy(chunks), self.keywords, self.laws, self.question, 5)
        after = ChunkSearchService.rank_chunks(copy.deepcopy(chunks), self.keywords, self.laws, self.question, 5, explicit_tax_domains=["부가가치세", "법인세"])
        self.assertEqual([(c.chunk_id,c.score) for c in before], [(c.chunk_id,c.score) for c in after])

    def test_input_spring_score_does_not_change_selection_and_order_is_stable(self):
        a = copy.deepcopy(self.chunks)
        b = copy.deepcopy(self.chunks)
        for chunk in a: chunk.score = 999999.0
        for chunk in b: chunk.score = -999999.0
        first = ChunkSearchService.rank_chunks(a, self.keywords, self.laws, self.question, 3, explicit_tax_domains=["부가가치세", "법인세"])
        second = ChunkSearchService.rank_chunks(b, self.keywords, self.laws, self.question, 3, explicit_tax_domains=["부가가치세", "법인세"])
        third = ChunkSearchService.rank_chunks(a, self.keywords, self.laws, self.question, 3, explicit_tax_domains=["부가가치세", "법인세"])
        self.assertEqual([(c.chunk_id,c.score) for c in first],[(c.chunk_id,c.score) for c in second])
        self.assertEqual([(c.chunk_id,c.score) for c in first],[(c.chunk_id,c.score) for c in third])
