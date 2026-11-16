import re
import unittest

from app.services.chunk_search_service import ChunkSearchService


class ExcerptPositionTest(unittest.TestCase):
    def assert_verified(self, text, limit, keywords, expected, expected_ranges=None):
        basic = ChunkSearchService.extract_relevant_excerpt(text, limit, keywords)
        detail = ChunkSearchService.extract_relevant_excerpt(
            text, limit, keywords, return_details=True,
        )
        self.assertEqual(expected, basic)
        self.assertEqual(basic, detail["excerpt"])
        self.assertEqual("verified", detail["position_status"])
        self.assertIsNone(detail["reason"])
        if expected_ranges is not None:
            self.assertEqual(expected_ranges, detail["ranges"])
        cursor = 0
        source_ranges = []
        for segment in detail["segments"]:
            start, end = segment["excerpt_range"]
            self.assertEqual(cursor, start)
            self.assertTrue(start < end <= len(expected))
            if segment["kind"] == "source":
                left, right = segment["source_range"]
                self.assertTrue(0 <= left < right <= len(text))
                self.assertEqual(expected[start:end], re.sub(r"\s+", " ", text[left:right]))
                source_ranges.append((left, right))
            else:
                self.assertEqual("generated", segment["kind"])
                self.assertIsNone(segment["source_range"])
                self.assertIn(expected[start:end], (" ", "..."))
            cursor = end
        self.assertEqual(len(expected), cursor)
        self.assertEqual(source_ranges, detail["ranges"])
        return detail

    def test_short_text(self):
        self.assert_verified("법령 근거", 30, [], "법령 근거", [(0, 5)])

    def test_whitespace_mapping(self):
        self.assert_verified(" \t가\t\n  나\r\n ", 30, [], "가 나", [(2, 8)])

    def test_empty_text(self):
        self.assert_verified("", 30, [], "", [])

    def test_only_whitespace(self):
        self.assert_verified(" \t\n", 30, [], "", [])

    def test_unanchored_window(self):
        self.assert_verified("abcdefghij", 4, [], "abcd...", [(0, 4)])

    def test_repeated_phrase_uses_selected_occurrence(self):
        self.assert_verified("xx 표적 yy 표적 zz", 5, ["표적"], "...표적 y...", [(3, 7)])

    def test_window_strip_boundaries(self):
        self.assert_verified("aa bb cc", 3, ["bb"], "...bb...", [(3, 5)])

    def test_original_dots_are_source(self):
        detail = self.assert_verified("... 원문 ...", 30, [], "... 원문 ...", [(0, 10)])
        self.assertEqual(["source"], [segment["kind"] for segment in detail["segments"]])

    def test_special_branch_multiple_ranges(self):
        text = " 제98조(부당행위계산의 부인) 공통요건 1. 제외 3. 특수관계인으로부터 용역 4. 제외 5. 단서 ③ 다음 "
        first = text.index("1.")
        third = text.index("3.")
        fourth = text.index("4.")
        fifth = text.index("5.")
        following = text.index("③")
        expected = "제98조(부당행위계산의 부인) 공통요건 3. 특수관계인으로부터 용역 5. 단서"
        detail = self.assert_verified(
            text, 10, ["특수관계인으로부터"], expected,
            [(1, first - 1), (third, fourth - 1), (fifth, following - 1)],
        )
        self.assertEqual(2, sum(segment["kind"] == "generated" for segment in detail["segments"]))

    def test_special_branch_missing_optional_items(self):
        text = "제98조(부당행위계산의 부인) 3. 특수관계인으로부터 용역"
        self.assert_verified(text, 4, ["특수관계인으로부터"], "3. 특수관계인으로부터 용역",
                             [(text.index("3."), len(text))])

    def test_unicode_length_change_is_unverified(self):
        text = "İabcdef 표적 xyz"
        basic = ChunkSearchService.extract_relevant_excerpt(text, 4, ["표적"])
        detail = ChunkSearchService.extract_relevant_excerpt(text, 4, ["표적"], return_details=True)
        self.assertEqual(basic, detail["excerpt"])
        self.assertEqual("unverified", detail["position_status"])
        self.assertIsNone(detail["ranges"])
        self.assertEqual([], detail["segments"])
        self.assertTrue(detail["reason"])

    def test_zero_window_preserves_generated_output(self):
        self.assert_verified("abcdef", 0, [], "...", [])


if __name__ == "__main__":
    unittest.main()
