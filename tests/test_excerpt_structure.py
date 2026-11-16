import unittest

from app.services.chunk_search_service import ChunkSearchService


class ExcerptStructureTest(unittest.TestCase):
    def details(self, text, limit=10000):
        result = ChunkSearchService.extract_relevant_excerpt(text, limit, [], return_details=True)
        self.assertEqual(ChunkSearchService.extract_relevant_excerpt(text, limit, []), result["excerpt"])
        for unit in result["structure_units"]:
            start, end = unit["source_range"]
            self.assertTrue(0 <= start < end <= len(text))
            self.assertTrue(text[start:end].startswith(unit["unit_label"]))
            if unit["parent_range"] is not None:
                left, right = unit["parent_range"]
                self.assertTrue(left <= start < end <= right)
            for left, right in unit["excerpt_overlaps"] or []:
                self.assertTrue(start <= left < right <= end)
        return result

    def test_single_paragraph_without_article_number(self):
        text = "① 본문이다."
        units = self.details(text)["structure_units"]
        self.assertEqual(1, len(units))
        self.assertEqual((0, len(text)), units[0]["source_range"])
        self.assertIsNone(units[0]["parent_range"])
        self.assertEqual("verified", units[0]["identification_status"])

    def test_multiple_paragraph_boundaries(self):
        text = "제5조(등록)\n① 첫째\n② 둘째"
        article, first, second = self.details(text)["structure_units"]
        self.assertEqual((text.index("①"), text.index("②")), first["source_range"])
        self.assertEqual((text.index("②"), len(text)), second["source_range"])
        self.assertEqual(article["source_range"], first["parent_range"])
        self.assertEqual(article["source_range"], second["parent_range"])

    def test_items_and_common_introduction(self):
        text = "① 다음 각 호에 따른다.\n1. 첫째\n2. 둘째"
        detail = self.details(text)
        paragraph, first, second = detail["structure_units"]
        self.assertEqual(paragraph["source_range"], first["parent_range"])
        self.assertEqual(paragraph["source_range"], second["parent_range"])
        introduction = detail["body_ranges"][0]
        self.assertEqual("common_introduction", introduction["kind"])
        self.assertEqual((1, text.index("1.")), introduction["source_range"])

    def test_subitems_under_item(self):
        text = "① 요건\n1. 구분\n가. 첫 목\n나. 둘째 목\n2. 다음 호"
        units = self.details(text)["structure_units"]
        paragraph, item, first, second, next_item = units
        self.assertEqual(["subitem", "subitem"], [first["unit_type"], second["unit_type"]])
        self.assertEqual(item["source_range"], first["parent_range"])
        self.assertEqual(item["source_range"], second["parent_range"])
        self.assertEqual(text.index("2."), second["source_range"][1])
        self.assertTrue(all(unit["identification_status"] == "verified" for unit in units))

    def test_branched_article_and_reference(self):
        text = "제19조의2(요건)\n① 제5조제2항에 따른다."
        detail = self.details(text)
        self.assertEqual("제19조의2", detail["structure_units"][0]["unit_label"])
        self.assertEqual(["제5조제2항"], [ref["expression"] for ref in detail["references"]])
        self.assertEqual("unverified", detail["references"][0]["identification_status"])

    def test_wrapped_reference_from_existing_fixture(self):
        text = "제87조(범위)\n① 다음 사유에 따른다.\n1. 「법인세법\n시행령」\n제19조의2제1항에\n따라 인정되는 경우\n2. 다른 사유"
        detail = self.details(text)
        self.assertEqual(4, len(detail["structure_units"]))
        reference = detail["references"][0]
        left, right = reference["source_range"]
        self.assertEqual("「법인세법\n시행령」\n제19조의2제1항", text[left:right])

    def test_qualifications_have_owner_but_no_inferred_target(self):
        text = "① 적용한다. 다만, 예외는 제외한다.\n1. 적용한다. 단, 조건은 제외한다."
        detail = self.details(text)
        paragraph, item = detail["structure_units"]
        qualifications = detail["qualifications"]
        self.assertEqual(paragraph["source_range"], qualifications[0]["owner_range"])
        second = next(q for q in qualifications if q["expression"].startswith("단,"))
        self.assertEqual(item["source_range"], second["owner_range"])
        for qualification in qualifications:
            self.assertIsNone(qualification["target_range"])
            self.assertEqual("unverified", qualification["identification_status"])
            left, right = qualification["source_range"]
            self.assertEqual(text[left:right], qualification["expression"])

    def test_headerless_body_has_no_invented_unit(self):
        detail = self.details("본문만 있다. 다만, 제외한다.")
        self.assertEqual([], detail["structure_units"])
        self.assertEqual("unverified", detail["body_ranges"][0]["identification_status"])
        self.assertIsNone(detail["qualifications"][0]["owner_range"])

    def test_inline_numbers_and_decimal_not_verified(self):
        detail = self.details("설명 1. 일반 문장이다. 값은 1.5이고 인용은 ①이다.")
        self.assertTrue(all(unit["identification_status"] == "unverified" for unit in detail["structure_units"]))
        self.assertFalse(any(unit["unit_label"] == "1.5" for unit in detail["structure_units"]))

    def test_incomplete_numbering_and_orphan_subitem(self):
        detail = self.details("③ 중간부터 시작\n2. 누락된 첫 호\n가. 목")
        self.assertTrue(all(unit["identification_status"] == "unverified" for unit in detail["structure_units"]))
        orphan = self.details("가. 상위 호 없음")["structure_units"][0]
        self.assertIsNone(orphan["parent_range"])
        self.assertEqual("unverified", orphan["identification_status"])

    def test_reference_not_treated_as_article_heading(self):
        detail = self.details("제5조제2항에 따라 적용한다.")
        self.assertEqual([], detail["structure_units"])
        self.assertEqual("제5조제2항", detail["references"][0]["expression"])

    def test_numbering_gap_taints_siblings_and_children(self):
        detail = self.details("① 요건\n1. 첫 호\n3. 누락\n4. 다음\n가. 목")
        self.assertEqual("verified", detail["structure_units"][0]["identification_status"])
        self.assertTrue(all(unit["identification_status"] == "unverified"
                            for unit in detail["structure_units"][1:]))

    def test_duplicate_paragraph_is_not_partially_verified(self):
        detail = self.details("① 처음\n① 중복\n② 다음")
        self.assertTrue(all(unit["identification_status"] == "unverified"
                            for unit in detail["structure_units"]))

    def test_excerpt_intersections_use_original_offsets(self):
        text = " \t① 첫째\n② 둘째"
        detail = self.details(text, 4)
        first, second = detail["structure_units"]
        self.assertEqual([(2, 6)], first["excerpt_overlaps"])
        self.assertEqual([], second["excerpt_overlaps"])

    def test_unverified_a1_does_not_claim_intersections(self):
        detail = self.details("① İabcdefgh", 4)
        self.assertEqual("unverified", detail["position_status"])
        self.assertIsNone(detail["structure_units"][0]["excerpt_overlaps"])


if __name__ == "__main__":
    unittest.main()
