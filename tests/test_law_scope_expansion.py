import contextlib
import io
import sys
import unittest
from app.schemas.retrieval import LawChunk
from app.services.chunk_search_service import ChunkSearchService as S


def doc(cid, law="법인세법", article="제23조", body="대통령령으로 정하는 감가상각비 요건을 적용한다."):
    return {"chunkId": cid, "lawName": law, "title": article + " 감가상각비", "content": article + "(감가상각비) " + body}


def conditions(laws=None, question="법인세법에서 감가상각비의 구체적인 요건은 무엇인가요?"):
    return {"original_question": question, "law_names": laws or ["법인세법"],
            "keywords": ["감가상각비"], "rewritten_query": "감가상각비", "concepts": [], "required_roles": []}


class RecordingClient:
    def __init__(self, initial, extra=None, fail=False):
        self.initial, self.extra, self.fail = initial, extra or {}, fail
        self.calls = []

    async def fetch_chunks(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            rows = self.initial
        elif self.fail:
            raise RuntimeError("supplement unavailable")
        else:
            rows = self.extra.get(kwargs["law_names"][0], [])
        return rows, {"page": 1, "size": 30, "total_pages": 1, "total_elements": len(rows)}


class ScopeConditionTest(unittest.TestCase):
    def test_base_law_requires_actual_delegation(self):
        c = conditions()
        plain = S._to_chunk(doc("plain", body="감가상각비는 장부에 계상한다."))
        self.assertEqual(S.build_supplemental_conditions(c, retrieved_chunks=[plain]), [])
        plans = S.build_supplemental_conditions(c, retrieved_chunks=[S._to_chunk(doc("root"))])
        self.assertEqual([p["law_names"] for p in plans], [["법인세법 시행령"]])
        self.assertEqual(plans[0]["parent_article"], "제23조")

    def test_decree_to_rule_uses_same_family_and_canonical_catalog(self):
        c = conditions(["법인세법 시행령"])
        chunk = S._to_chunk(doc("decree", "법인세법 시행령", "제24조", "감가상각비 요건은 재정경제부령으로 정한다."))
        self.assertEqual(S.build_supplemental_conditions(c, retrieved_chunks=[chunk])[0]["law_names"], ["법인세법 시행규칙"])
        self.assertEqual(S.build_supplemental_conditions(conditions(["미수집법"]), retrieved_chunks=[chunk]), [])

    def test_unrelated_title_and_rule_lookup_do_not_expand(self):
        chunk = S._to_chunk(doc("root"))
        self.assertEqual(S.build_supplemental_conditions(conditions(question="신고 기한은 언제인가요?"), retrieved_chunks=[chunk]), [])
        self.assertEqual(S.build_supplemental_conditions(conditions(question="어떤 규정이 감가상각비 제한에 적용되나요?"), retrieved_chunks=[chunk]), [])
        self.assertEqual(S.build_supplemental_conditions(conditions(question="기부금 요건은 무엇인가요?"), retrieved_chunks=[chunk]), [])

    def test_visited_duplicate_and_existing_scope_do_not_repeat(self):
        root = S._to_chunk(doc("root"))
        plans = S.build_supplemental_conditions(conditions(), retrieved_chunks=[root, root])
        self.assertEqual(len(plans), 1)
        self.assertEqual(S.build_supplemental_conditions(conditions(), retrieved_chunks=[root], visited={tuple(plans[0]["request_key"])}), [])
        self.assertEqual(S.build_supplemental_conditions(conditions(["법인세법", "법인세법 시행령"]), retrieved_chunks=[root]), [])

    def test_existing_question_driven_phase_keeps_old_conditions(self):
        c = conditions()
        self.assertEqual(S.build_supplemental_conditions(c), [])
        c["concepts"] = [{"role": "TIME_CONDITION", "search_terms": ["신고기한", "신고하여야"]}]
        c["required_roles"] = ["TIME_CONDITION"]
        self.assertEqual(len(S.build_supplemental_conditions(c)), 1)
        self.assertEqual(len(S.build_supplemental_conditions(c, retrieved_chunks=[S._to_chunk(doc("root"))])), 1)


class ScopeRetrievalTest(unittest.IsolatedAsyncioTestCase):
    async def retrieve(self, c, client):
        previous = sys.getprofile()
        def observe(frame, event, result):
            if event == "return" and frame.f_code.co_name == "retrieve_chunks" and isinstance(result, tuple):
                self.observed_added_ids = set(frame.f_locals.get("scope_added_ids", set()))
                self.observed_candidates = len(frame.f_locals.get("chunks", []))
        sys.setprofile(observe)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return await S(client=client, candidate_size=30).retrieve_chunks(c, top_k=5)
        finally:
            sys.setprofile(previous)

    async def test_merge_dedup_and_allow_confirmed_subordinate(self):
        child = doc("child", "법인세법 시행령", "제24조", "법 제23조에 따른 감가상각비 요건을 정한다.")
        wrong = doc("wrong", "법인세법 시행령", "제25조", "법 제99조에 따른 감가상각비를 정한다.")
        client = RecordingClient([doc("root")], {"법인세법 시행령": [child, child, wrong]})
        ranked, _ = await self.retrieve(conditions(), client)
        self.assertEqual({c.chunk_id for c in ranked}, {"root", "child"})
        self.assertTrue(all(c["candidate_size"] == 30 for c in client.calls))
        self.assertEqual(len(client.calls), 2)

    async def test_dependency_failure_preserves_initial_result(self):
        client = RecordingClient([doc("root")], fail=True)
        ranked, _ = await self.retrieve(conditions(), client)
        self.assertEqual([c.chunk_id for c in ranked], ["root"])
        self.assertEqual(len(client.calls), 2)

    async def test_existing_supplement_failure_preserves_primary(self):
        c = conditions(question="감가상각비 신고 기한은 언제인가요?")
        c["concepts"] = [{"role": "TIME_CONDITION", "search_terms": ["신고기한", "신고하여야"]}]
        c["required_roles"] = ["TIME_CONDITION"]
        ranked, _ = await self.retrieve(c, RecordingClient([doc("root", body="감가상각비는 신고기한까지 신고하여야 한다.")], fail=True))
        self.assertEqual([x.chunk_id for x in ranked], ["root"])

    async def test_wrong_parent_prefix_and_missing_article_are_not_accepted(self):
        child = doc("wrong", "법인세법 시행령", "제24조", "법 제23조의2에 따른 감가상각비 요건이다.")
        ranked, _ = await self.retrieve(conditions(), RecordingClient([doc("root")], {"법인세법 시행령": [child]}))
        self.assertEqual([x.chunk_id for x in ranked], ["root"])

    async def test_no_unnecessary_call_for_no_delegation(self):
        client = RecordingClient([doc("root", body="감가상각비는 장부에 계상한다.")])
        ranked, _ = await self.retrieve(conditions(), client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(ranked), 1)

    async def test_new_candidate_and_request_budgets(self):
        laws = ["법인세법", "소득세법", "부가가치세법", "개별소비세법"]
        initial = [doc("root" + str(i), law) for i, law in enumerate(laws)]
        extra = {law + " 시행령": [doc(str(i) + "-" + str(j), law + " 시행령", "제24조", "법 제23조에 따른 감가상각비 요건이다.") for j in range(30)] for i, law in enumerate(laws)}
        client = RecordingClient(initial, extra)
        ranked, _ = await self.retrieve(conditions(laws), client)
        self.assertLessEqual(len(client.calls) - 1, 4)
        self.assertLessEqual(len(ranked), 5)
        self.assertLessEqual(len(self.observed_added_ids), 60)
        self.assertLessEqual(self.observed_candidates, 120)


class CrossReferenceTest(unittest.IsolatedAsyncioTestCase):
    def source(self, text):
        return LawChunk(chunk_id="source", law_name="부가가치세법 시행령", title="제87조 대손세액 공제의 범위", content="제87조(대손세액 공제의 범위)\n" + text)

    def cond(self):
        return conditions(["부가가치세법 시행령"], "대손세액 공제의 요건은 무엇인가요?")

    def test_canonical_law_and_article_are_paired_in_same_span(self):
        src = self.source("「소득세법 시행령」 제55조제2항 및 「법인세법시행령」 제19조의2제1항에 따라 대손금으로 인정되는 사유")
        plans = S.build_supplemental_conditions(self.cond(), retrieved_chunks=[src])
        self.assertEqual({(p["law_names"][0], p["target_article"]) for p in plans}, {("소득세법 시행령", "제55조"), ("법인세법 시행령", "제19조의2")})

    def test_uncollected_or_mention_only_reference_does_not_expand(self):
        for body in ["「미수집법 시행령」 제99조에 따라 인정한다.", "「법인세법 시행령」 제99조를 참고자료로 표시한다."]:
            self.assertEqual(S.build_supplemental_conditions(self.cond(), retrieved_chunks=[self.source(body)]), [])

    async def test_or_search_cannot_substitute_another_article_for_reference(self):
        src = self.source("「법인세법 시행령」 제999조에 따라 대손 사유를 인정한다.")
        raw = {"chunkId": src.chunk_id, "lawName": src.law_name, "title": src.title, "content": src.content}
        other = doc("wrong-article", "법인세법 시행령", "제23조", "제999조를 인용하는 대손세액 공제 요건")
        client = RecordingClient([raw], {"법인세법 시행령": [other]})
        with contextlib.redirect_stdout(io.StringIO()):
            ranked, _ = await S(client, 30).retrieve_chunks(self.cond(), 5)
        self.assertEqual([x.chunk_id for x in ranked], ["source"])
        self.assertEqual(len(client.calls), 2)

    async def test_direct_reference_is_accepted_with_original_candidate(self):
        src = self.source("「법인세법 시행령」 제19조의2제1항에 따라 대손 사유를 인정한다.")
        raw = {"chunkId": src.chunk_id, "lawName": src.law_name, "title": src.title, "content": src.content}
        target = doc("reference", "법인세법 시행령", "제19조의2", "대손세액 공제의 요건에 해당하는 사유")
        client = RecordingClient([raw], {"법인세법 시행령": [target]})
        with contextlib.redirect_stdout(io.StringIO()):
            ranked, _ = await S(client, 30).retrieve_chunks(self.cond(), 5)
        self.assertEqual({x.chunk_id for x in ranked}, {"source", "reference"})


class ConservativeScopeTest(unittest.TestCase):
    def test_generic_short_topics_without_case_facts_do_not_trigger_more_calls(self):
        q = conditions(["부가가치세법"], "부가가치세법에서 재화의 공급 시기와 용역의 공급 시기는 어떤 기준으로 판단하나요?")
        seed = LawChunk(chunk_id="seed", law_name="부가가치세법", title="제17조 재화 및 용역의 공급시기", content="제17조(공급시기) 대통령령으로 정하는 사유를 적용한다.")
        self.assertEqual(S.build_supplemental_conditions(q, retrieved_chunks=[seed]), [])


class ScopeReviewRegressionTest(unittest.TestCase):
    def test_four_non_delegating_articles_do_not_hide_lower_delegation(self):
        q = conditions()
        sources = [S._to_chunk(doc("complete" + str(i), article="제" + str(i + 1) + "조", body="감가상각비의 요건을 직접 규정한다.")) for i in range(4)]
        sources.append(S._to_chunk(doc("delegated", article="제23조")))
        plans = S.build_supplemental_conditions(q, retrieved_chunks=sources)
        self.assertEqual(plans[0]["parent_article"], "제23조")

    def test_invalid_citation_seeds_do_not_consume_request_budget(self):
        q = conditions()
        sources = [S._to_chunk(doc(str(i), article="제" + str(i + 1) + "조", body="「민법」 제1조를 참고한다.")) for i in range(5)]
        sources.append(S._to_chunk(doc("delegated", article="제23조")))
        plans = S.build_supplemental_conditions(q, retrieved_chunks=sources)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["parent_article"], "제23조")

    def test_multiple_dependencies_in_one_law_are_not_collapsed_to_one_seed(self):
        q = conditions()
        sources = [S._to_chunk(doc("a", article="제23조")), S._to_chunk(doc("b", article="제24조"))]
        self.assertEqual(len(S.build_supplemental_conditions(q, retrieved_chunks=sources)), 2)

    def test_wrapped_canonical_citation_and_reason_remain_one_clause(self):
        q = conditions(["부가가치세법 시행령"], "대손세액 공제의 요건은 무엇인가요?")
        seed = LawChunk(chunk_id="source", law_name="부가가치세법 시행령", title="제87조 대손세액 공제의 범위", content="제87조(대손세액 공제의 범위)\n① 다음 사유에 따른다.\n1. 「법인세법\n시행령」\n제19조의2제1항에\n따라 대손금으로 인정되는 경우\n2. 다른 사유")
        plans = S.build_supplemental_conditions(q, retrieved_chunks=[seed])
        self.assertEqual([(p["law_names"], p["target_article"]) for p in plans], [(["법인세법 시행령"], "제19조의2")])

    def test_related_word_in_neighboring_item_cannot_validate_reference(self):
        q = conditions(["부가가치세법 시행령"], "대손세액 공제의 요건은 무엇인가요?")
        seed = LawChunk(chunk_id="source", law_name="부가가치세법 시행령", title="제87조 대손세액 공제의 범위", content="제87조(대손세액 공제의 범위)\n1. 등록 절차는 「법인세법 시행령」 제19조의2에 따라 인정한다.\n2. 대손금 인정 사유")
        self.assertEqual(S.build_supplemental_conditions(q, retrieved_chunks=[seed]), [])
