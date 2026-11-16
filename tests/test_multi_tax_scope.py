import unittest
from app.services.chunk_search_service import ChunkSearchService

Q12 = '내국법인이 과세사업용 재화를 매입했고 매입세액 불공제 사유가 없다고 가정할 때, 부가가치세 매입세액 공제 원칙과 그 세액의 법인세 손금불산입 원칙을 각각 어디서 확인하나요?'
SINGLE_CASES = [('법인사업자의 부가가치세 신고기한은 언제인가요?', '부가가치세', ['부가가치세법', '부가가치세법 시행령', '부가가치세법 시행규칙']), ('법인세 신고기한은 언제인가요?', '법인세', ['법인세법', '법인세법 시행령', '법인세법 시행규칙']), ('부가가치세 매입세액을 공제할 수 있나요?', '부가가치세', ['부가가치세법', '부가가치세법 시행령', '부가가치세법 시행규칙']), ('일반적인 공제 요건을 알려 주세요.', '세무일반', []), ('상속세 및 증여세법 제53조 공제한도는 얼마인가요?', '세무일반', ['상속세 및 증여세법']), ('소득세 확정신고 기한은 언제인가요?', '부가가치세', ['부가가치세법', '부가가치세법 시행령', '부가가치세법 시행규칙']), ('종합소득 확정신고 기한은 언제인가요?', '소득세', ['소득세법', '소득세법 시행령', '소득세법 시행규칙']), ('내국법인의 공제와 신고 요건은 무엇인가요?', '법인세', ['법인세법', '법인세법 시행령', '법인세법 시행규칙']), ('개인 프리랜서가 법인을 설립해 사업소득을 분산하면 어떻게 되나요?', '소득세', ['소득세법', '소득세법 시행령', '소득세법 시행규칙'])]


class MultiTaxScopeTest(unittest.TestCase):
    def setUp(self):
        self.service = ChunkSearchService(client=None, candidate_size=30)

    def test_two_direct_tax_names_expand_scope_without_changing_primary_domain(self):
        c = self.service.build_search_conditions(Q12)
        self.assertEqual(["부가가치세", "법인세"], c["explicit_tax_domains"])
        self.assertEqual("법인세", c["tax_domain"])
        self.assertEqual(["부가가치세법", "법인세법"], c["law_names"])
        self.assertEqual([], c["catalog_missing_law_names"])
        self.assertIn("부가가치세법", c["rewritten_query"])
        self.assertIn("법인세법", c["rewritten_query"])
        self.assertEqual([], self.service.build_supplemental_conditions(c))

    def test_original_mention_order_and_abbreviation(self):
        for q, expected in [
            ("법인세와 부가가치세의 신고기한을 비교해 주세요.", ["법인세법", "부가가치세법"]),
            ("부가세와 법인세의 신고기한은 언제인가요?", ["부가가치세법", "법인세법"]),
            ("개별  소비세와 부가가치세가 어떻게 다른가요?", ["개별소비세법", "부가가치세법"]),
        ]:
            with self.subTest(question=q):
                self.assertEqual(expected, self.service.build_search_conditions(q)["law_names"])

    def test_single_domain_and_generic_words_keep_prior_scope(self):
        for question, domain, laws in SINGLE_CASES:
            with self.subTest(question=question):
                c = self.service.build_search_conditions(question)
                self.assertEqual(domain, c["tax_domain"])
                self.assertEqual(laws, c["law_names"])
                self.assertNotIn("explicit_tax_domains", c)
        c = self.service.build_search_conditions("세금계산서와 법인세 공제 요건은 무엇인가요?")
        self.assertEqual(self.service.infer_law_names(c["tax_domain"]), c["law_names"])
        self.assertNotIn("explicit_tax_domains", c)

    def test_explicit_canonical_names_and_level_take_priority(self):
        c = self.service.build_search_conditions("법인세법과 부가가치세법의 신고기한을 비교해 주세요.")
        self.assertEqual(["법인세법", "부가가치세법"], c["law_names"])
        c = self.service.build_search_conditions("법인세법 시행령에 따른 비용과 부가가치세의 신고를 비교해 주세요.")
        self.assertEqual(["법인세법 시행령", "부가가치세법"], c["law_names"])
        self.assertNotIn("법인세법", c["law_names"])

    def test_uncollected_names_are_retained_and_identified(self):
        c = self.service.build_search_conditions("국세기본법과 법인세 및 부가가치세를 비교해 주세요.")
        self.assertEqual(["국세기본법", "법인세법", "부가가치세법"], c["law_names"])
        self.assertEqual(["국세기본법"], c["catalog_missing_law_names"])
        c = self.service.build_search_conditions("지방세와 부가가치세 신고를 비교해 주세요.")
        local_laws = self.service.infer_law_names("지방세")
        self.assertEqual(local_laws + ["부가가치세법"], c["law_names"])
        self.assertEqual(local_laws, c["catalog_missing_law_names"])
        self.assertEqual(len(c["law_names"]), len(set(c["law_names"])))

    def test_expansion_does_not_come_from_expanded_keywords(self):
        c = self.service.build_search_conditions("사업소득과 법인세 공제 요건은 무엇인가요?")
        self.assertNotIn("explicit_tax_domains", c)
        self.assertIsInstance(self.service.infer_tax_domain(c["keywords"]), str)
        self.assertEqual(self.service.infer_law_names(c["tax_domain"]), c["law_names"])
