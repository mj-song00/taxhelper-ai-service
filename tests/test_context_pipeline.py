"""실제 prepare_chat→payload 경로의 비LLM 근거 전달 검증."""
import asyncio
import ast
import copy
from contextlib import redirect_stdout
import difflib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
from time import perf_counter
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.core.config import get_settings
from app.routers.retrieval import NOMINEE_BUSINESS_BASIS, prepare_chat, build_sources
from app.schemas.retrieval import ChatRequest, PrecedentChunk
from app.services.chunk_search_service import ChunkSearchService as S
from app.services.llm_service import LlmService
from app.services.precedent_search_service import PrecedentSearchService as P

REFERENCE = Path(os.getenv('TAXHELPER_D_REFERENCE', '/Users/songminji/Documents/TaxHelper-benchmarks/runs/20261116T130804928320+0900_joint-optional-budget_83a71831042c4f74beb9c5500d4808be'))
REQUEST_SUFFIX = '\n\n위 검색 근거만 사용해서 사용자의 질문에 답변해줘. 검색 근거와 관련 없는 일반 지식은 사용하지 마.\n/no_think'


def load_evaluator():
    # 평가 함수만 사용하며 실험의 D 선정/실행 함수를 호출하지 않는다.
    spec=importlib.util.spec_from_file_location('frozen_evidence_evaluator',REFERENCE/'replay.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


async def pipeline(question, sources, configured_limit=None):
    settings=get_settings()
    if configured_limit is not None:
        settings=settings.model_copy(update={'ollama_max_context_chars':configured_limit})
    law=S(client=None,candidate_size=settings.default_candidate_size)
    precedent=P(client=None,candidate_size=settings.default_candidate_size)
    chunks=[S._to_chunk(s) for s in sources['law']]
    cases=[PrecedentChunk(**s) for s in sources.get('precedent',[])]
    before=copy.deepcopy(chunks)
    law.retrieve_chunks=AsyncMock(return_value=(chunks,None))
    precedent.retrieve_chunks=AsyncMock(return_value=(cases,None))
    def blocked(*args,**kwargs):
        raise AssertionError('테스트 중 실제 네트워크/검색/LLM 호출 금지')
    with patch('app.core.config.get_settings',return_value=settings), \
         patch('app.routers.retrieval.get_settings',return_value=settings), \
         patch('app.services.llm_service.get_settings',return_value=settings), \
         patch.object(socket.socket,'connect',blocked), patch('socket.create_connection',blocked), \
         patch.object(httpx.AsyncClient,'send',side_effect=blocked), \
         patch.object(LlmService,'generate_answer',side_effect=blocked), \
         patch.object(LlmService,'warm_up',side_effect=blocked), redirect_stdout(io.StringIO()):
        prepared=await prepare_chat(ChatRequest(question=question,top_k=5),'offline-test',perf_counter(),law,precedent)
        llm=LlmService()
        try:
            payload=llm._build_payload(question,prepared.context)
        finally:
            await llm.close()
    message=payload['messages'][1]['content']
    prefix=f'[사용자 질문]\n{question.strip()}\n\n[검색 근거]\n'
    assert message.startswith(prefix) and message.endswith(REQUEST_SUFFIX)
    delivered=message[len(prefix):-len(REQUEST_SUFFIX)]
    assert chunks==before, '검색 결과·순위·점수가 변했습니다.'
    assert payload['model']==settings.ollama_model and payload['options']['num_ctx']==settings.ollama_num_ctx
    assert len(prepared.context)<=min(3000,settings.ollama_max_context_chars)
    assert prepared.context==delivered, 'payload에서 검색 근거 문자열이 변했습니다.'
    return prepared,payload,delivered,law.retrieve_chunks.call_args.kwargs,settings


def verify_units(sources, details):
    for block in details['blocks']:
        source=sources['law'][block['search_rank']-1]
        assert str(source['chunk_id'])==block['chunk_id']
        assert all(0<=a<b<=len(source['content']) for a,b in block['ranges'])
        text=' ... '.join(' '.join(source['content'][a:b].split()) for a,b in block['ranges'])
        assert text==block['excerpt'] and text in details['context']
        assert (block['classification']=='complete_unit')==(block['structure_status']=='verified')
        assert block['connections']['legal_applicability']=='unverified'


def first_difference(left,right):
    if left==right:
        return None
    index=next((i for i,(a,b) in enumerate(zip(left,right)) if a!=b),min(len(left),len(right)))
    return {'char_index':index,'expected_chars':len(left),'actual_chars':len(right),
            'expected_near':left[max(0,index-30):index+60], 'actual_near':right[max(0,index-30):index+60]}


class ContextPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_ten_service_to_payload_and_frozen_D(self):
        evaluator=load_evaluator()
        results={}
        for path in sorted((REFERENCE/'outputs').glob('*.json')):
            with self.subTest(question=path.stem):
                prior=json.loads(path.read_text())
                sources=json.loads((REFERENCE/'inputs'/path.stem/'sources.json').read_text())
                prepared,payload,delivered,search,_=await pipeline(prior['question'],sources)
                self.assertEqual(prior['variants']['D']['context'],prepared.context)
                self.assertEqual(prepared.context,delivered)
                self.assertEqual(json.loads((REFERENCE/'inputs'/path.stem/'conditions.json').read_text()),search['conditions'])
                verify_units(sources,prepared.context_details)
                required=[{k:c[k] for k in ('chunk_id','source_range','text','purpose')}
                          for c in prior['evaluation_v2']['D']['checks']]
                actual=evaluator.evaluate(sources,{'D':prepared.context_details},required)['D']
                self.assertEqual(prior['evaluation_v2']['D'],actual)
                results[path.stem]=actual['classification']
        self.assertEqual(8,sum(v=='complete_unit' for v in results.values()))
        self.assertEqual('limited_quote',results['Q01'])
        self.assertEqual('not_preserved',results['Q18'])

    async def test_lower_setting_and_hard_3000_cap(self):
        prior=json.loads((REFERENCE/'outputs/Q01.json').read_text())
        sources=json.loads((REFERENCE/'inputs/Q01/sources.json').read_text())
        for limit in (1000,2000,20000):
            with self.subTest(limit=limit):
                prepared,_,delivered,_,_=await pipeline(prior['question'],sources,limit)
                self.assertLessEqual(len(delivered),min(limit,3000))
                self.assertEqual(prepared.context,delivered)
                verify_units(sources,prepared.context_details)

    async def test_empty_and_unstructured_candidates_have_no_cut_fallback(self):
        for sources in ({'law':[]},{'law':[{'chunk_id':'unknown','law_name':'시험법','content':'공제 '*1500}]}):
            prepared,_,delivered,_,_=await pipeline('공제 요건',sources)
            self.assertEqual([],prepared.context_details['blocks'])
            self.assertNotIn('공제 공제',delivered)
            self.assertEqual([],prepared.law_chunk_data)

    async def test_outer_reservation_overflow_is_explicit(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as error:
            await pipeline('명의상 사업자와 실제 운영자 중 납세의무자는 누구인가요?',{'law':[]},20)
        self.assertEqual(503,error.exception.status_code)

    async def test_supplement_is_reserved_before_D_selection(self):
        prior=json.loads((REFERENCE/'outputs/Q01.json').read_text())
        sources=json.loads((REFERENCE/'inputs/Q01/sources.json').read_text())
        question='명의상 사업자와 실제 운영자 중 납세의무자는 누구인가요? 종합소득 신고 기한도 알려주세요.'
        prepared,_,delivered,_,_=await pipeline(question,sources,1000)
        self.assertTrue(delivered.startswith('[보충 법령 근거]\n'+NOMINEE_BUSINESS_BASIS+'\n\n'))
        self.assertEqual('curated-national-tax-basic-act-14',prepared.law_chunk_data[0]['chunk_id'])
        self.assertEqual('joint-optional-budget-v1',prepared.context_details['input_version'])
        verify_units(sources,prepared.context_details)

    async def test_precedent_priority_and_supplement_preserved_without_D(self):
        sources={'law':[{'chunk_id':'law','law_name':'시험법','title':'제1조 공제','content':'제1조(공제)\n① 공제한다.'}],
                 'precedent':[{'chunk_id':'case1','precedent_id':'case-1','title':'공제 판결',
                               'content':'공제 법리와 사건 결론입니다.','metadata':{'caseNumber':'2020두1234','courtName':'대법원'}}]}
        for question in ('공제 판례를 알려주세요.','명의상 사업자와 실제 운영자 중 납세의무자는 누구인가요? 판례도 알려주세요.'):
            prepared,_,delivered,_,_=await pipeline(question,sources)
            self.assertNotEqual('joint-optional-budget-v1',prepared.context_details['input_version'])
            self.assertLess(delivered.index('[판례 근거]'),delivered.index('[법령 근거]'))
            self.assertIn('공제 법리와 사건 결론입니다.',delivered)
            self.assertIn('① 공제한다.',delivered)
            self.assertEqual(1,len(prepared.precedent_chunks))
            if question.startswith('명의상'):
                self.assertIn(NOMINEE_BUSINESS_BASIS,delivered)

    async def test_precedent_long_metadata_excludes_whole_blocks(self):
        sources={'law':[{'chunk_id':'law','law_name':'시험법','content':'제1조(공제)\n① 공제한다.'}],
                 'precedent':[{'chunk_id':'big','title':'메타데이터'*900,'content':'공제 첫째'},
                              {'chunk_id':'small','title':'공제 사건','content':'공제 둘째 전체 결론'}]}
        prepared,_,delivered,_,_=await pipeline('공제 판례를 알려주세요.',sources,1000)
        self.assertEqual(1,prepared.context_details['precedent_excluded'])
        self.assertNotIn('공제 첫째',delivered)
        self.assertIn('공제 둘째 전체 결론',delivered)
        self.assertEqual(['small'],[c.chunk_id for c in prepared.precedent_chunks])

    async def test_precedent_law_excluded_instead_of_final_slice(self):
        sources={'law':[{'chunk_id':'law','law_name':'시험법','title':'긴 법령 출처'*500,'content':'제1조(공제)\n① 공제한다.'}],
                 'precedent':[{'chunk_id':'case','content':'공제 판례의 전체 발췌입니다.'}]}
        prepared,_,delivered,_,_=await pipeline('공제 판례를 알려주세요.',sources,1000)
        self.assertEqual(1,prepared.context_details['law_excluded'])
        self.assertIn('공제 판례의 전체 발췌입니다.',delivered)
        self.assertEqual([],prepared.law_chunk_data)
        self.assertNotIn('① 공제한다.',delivered)

    async def test_source_cards_keep_unverified_labels_and_ranges(self):
        prior=json.loads((REFERENCE/'outputs/Q01.json').read_text())
        sources=json.loads((REFERENCE/'inputs/Q01/sources.json').read_text())
        prepared,_,_,_,_=await pipeline(prior['question'],sources)
        cards=build_sources(prepared.law_chunk_data,prepared.precedent_chunk_data)
        units=[u for card in cards for u in card['evidence_units']]
        self.assertTrue(any(u['classification']=='limited_quote' for u in units))
        for card in cards:
            if any(u['classification']=='limited_quote' for u in card['evidence_units']):
                self.assertIn('[제한적 인용:',card['excerpt'])

    async def test_real_service_ID_invariance_and_determinism(self):
        for qid in ('Q01','Q12','Q20'):
            prior=json.loads((REFERENCE/'outputs'/f'{qid}.json').read_text())
            sources=json.loads((REFERENCE/'inputs'/qid/'sources.json').read_text())
            first=await pipeline(prior['question'],sources)
            renamed=copy.deepcopy(sources)
            for i,chunk in enumerate(renamed['law']):
                chunk['chunk_id']='opaque-'+str(i)
            second=await pipeline(prior['question'],renamed)
            again=await pipeline(prior['question'],sources)
            self.assertEqual(first[0].context,second[0].context)
            self.assertEqual(first[0].context,again[0].context)


class PayloadDefenseTest(unittest.TestCase):
    def test_payload_rejects_overflow_without_slicing(self):
        llm=object.__new__(LlmService)
        llm.max_context_chars=3000
        with self.assertRaises(ValueError):
            llm._build_payload('질문','[법령 근거]\n'+'가'*3001)
        llm.max_context_chars=20000
        with self.assertRaises(ValueError):
            llm._build_payload('질문','가'*3001)

    def test_structured_retry_has_no_character_reduction(self):
        # stream_answer는 실행하지 않고 실제 조건식을 평가해 구조 근거 재시도 금지를 검증한다.
        source=Path(sys.modules[LlmService.__module__].__file__).read_text()
        tree=ast.parse(source)
        condition=next(n.test for n in ast.walk(tree) if isinstance(n,ast.If)
                       and isinstance(n.test,ast.BoolOp) and 'exceed_context_size_error' in ast.unparse(n.test))
        compiled=compile(ast.Expression(condition),'retry-condition','eval')
        for context in ('[법령 근거]\n근거','[판례 근거]\n판례','[보충 법령 근거]\n근거','[제한적 인용: 미확인]'):
            namespace=dict(error_body='exceed_context_size_error',_context_retry=False,context=context)
            self.assertFalse(eval(compiled,namespace,namespace))
        namespace=dict(error_body='exceed_context_size_error',_context_retry=False,context='과거 비구조 입력')
        self.assertTrue(eval(compiled,namespace,namespace))


async def evidence_main(root):
    evaluator=load_evaluator()
    rows=[]
    def save(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8') as stream:
            stream.write(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2,default=list))
    for file in sorted((REFERENCE/'outputs').glob('*.json')):
        qid=file.stem; prior=json.loads(file.read_text())
        sources=json.loads((REFERENCE/'inputs'/qid/'sources.json').read_text())
        prepared,payload,delivered,search,settings=await pipeline(prior['question'],sources)
        verify_units(sources,prepared.context_details)
        expected=prior['variants']['D']['context']
        differences={'experiment_to_prepare':first_difference(expected,prepared.context),
                     'prepare_to_payload':first_difference(prepared.context,delivered)}
        required=[{k:c[k] for k in ('chunk_id','source_range','text','purpose')} for c in prior['evaluation_v2']['D']['checks']]
        evaluated=evaluator.evaluate(sources,{'D':prepared.context_details},required)['D']
        save(root/'contexts'/qid/'experiment-D.txt',expected)
        save(root/'contexts'/qid/'prepare.txt',prepared.context)
        save(root/'contexts'/qid/'payload-evidence.txt',delivered)
        save(root/'outputs'/f'{qid}.json',{'question':prior['question'],'context_details':prepared.context_details,
             'payload':payload,'differences':differences,'evaluation':evaluated,
             'reference_evaluation':prior['evaluation_v2']['D'], 'injected_search_sources':sources,
             'search_call':search,'source_cards':build_sources(prepared.law_chunk_data,prepared.precedent_chunk_data)})
        assert evaluated==prior['evaluation_v2']['D']
        assert all(v is None for v in differences.values()), differences
        rows.append({'question_id':qid,'chars':len(delivered),'experiment_equal':expected==prepared.context,
                     'payload_equal':prepared.context==delivered,'classification':evaluated['classification'],
                     'complete_unit_preserved':evaluated['complete_unit_preserved'],
                     'required_text_preserved':evaluated['required_text_preserved']})
    totals={'complete_unit':sum(r['complete_unit_preserved'] for r in rows),
            'limited_quote_only':sum(r['classification']=='limited_quote' for r in rows),
            'required_text_preserved':sum(r['required_text_preserved'] for r in rows)}
    regressions=[r['question_id'] for r in rows if r['question_id']!='Q18' and not r['required_text_preserved']]
    save(root/'pipeline-summary.json',{'cases':rows,'totals':totals,'regressions':regressions,
         'llm_called':False,'search_server_called':False,'DB_called':False,
         'model_settings':{k:getattr(settings,k) for k in ('ollama_model','ollama_num_ctx','ollama_max_context_chars','ollama_num_predict','ollama_keep_alive')}})
    assert totals=={'complete_unit':8,'limited_quote_only':1,'required_text_preserved':9} and not regressions


if __name__=='__main__':
    if '--evidence' in sys.argv:
        asyncio.run(evidence_main(Path(os.environ['TAXHELPER_E2E_ROOT'])))
    else:
        unittest.main()
