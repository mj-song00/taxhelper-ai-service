import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.services.chunk_search_service import ChunkSearchService, _load_law_name_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "app/data/law_names_catalog_v1.json"
SOURCE = ROOT / "app/services/chunk_search_service.py"
CASES = [('G01', '내국법인', []), ('G02', '외국법인', []), ('G03', '법인', []), ('V01', '법인세법', ['법인세법']), ('V02', '법인세법상', ['법인세법']), ('V03', '법인세법에서', ['법인세법']), ('C01', '상속세 및 증여세법', ['상속세 및 증여세법']), ('C02', '상속세  및   증여세법', ['상속세 및 증여세법']), ('C03', '상속세\t및\t증여세법', ['상속세 및 증여세법']), ('C04', '상속세및증여세법', ['상속세 및 증여세법']), ('C05', '상속세 및증여세법', ['상속세 및 증여세법']), ('C06', '상속세 및 증여세법의 제53조', ['상속세 및 증여세법']), ('C07', '상속세 및 증여세법상', ['상속세 및 증여세법']), ('L01', '법인세법 시행령', ['법인세법 시행령']), ('L02', '법인세법시행령', ['법인세법 시행령']), ('L03', '법인세법 시행규칙', ['법인세법 시행규칙']), ('L04', '소득세법 시행령', ['소득세법 시행령']), ('M01', '법인세법과 부가가치세법', ['법인세법', '부가가치세법']), ('M02', '국세기본법과 법인세법', ['국세기본법', '법인세법']), ('N01', '위법 여부', ['위법']), ('N02', '상속세 관련 문제 및 증여세법을 설명', ['증여세법']), ('N03', '가짜법인세법문서', []), ('N04', '법인세법인', []), ('U01', '국세기본법', ['국세기본법']), ('C08', '「상속세 및 증여세법」 제53조', ['상속세 및 증여세법']), ('GAP01', '상속세\n및 증여세법', ['증여세법']), ('GAP02', '법인세법 시행령과 시행규칙', ['법인세법 시행령'])]


class CanonicalLawNameTest(unittest.TestCase):
    def test_existing_boundary_cases_on_operating_method(self):
        for case_id, question, expected in CASES:
            with self.subTest(case=case_id):
                self.assertEqual(expected, ChunkSearchService.extract_law_names(question))

    def test_duplicates_and_explicit_level_names(self):
        self.assertEqual(["법인세법", "법인세법 시행령", "법인세법 시행규칙"],
            ChunkSearchService.extract_law_names("법인세법과 법인세법 시행령 및 법인세법 시행규칙, 법인세법"))
        self.assertEqual([], ChunkSearchService.extract_law_names("법인세법에따르면"))

    def test_existing_fallback_and_unresolved_scope(self):
        service = ChunkSearchService(client=None, candidate_size=30)
        c = service.build_search_conditions("내국법인은 법인세를 언제 신고하나요?")
        self.assertEqual(service.infer_law_names("법인세"), c["law_names"])
        c = service.build_search_conditions("상속세 및 증여세법 제53조의 공제는 무엇인가요?")
        self.assertEqual(["상속세 및 증여세법"], c["law_names"])
        c = service.build_search_conditions("국세기본법 제14조를 알려 주세요")
        self.assertEqual(["국세기본법"], c["law_names"])
        self.assertIn("국세기본법", c["rewritten_query"])

    def test_runtime_catalog_matches_original(self):
        self.assertEqual(CATALOG.read_bytes(), (ROOT / "docs/search-evaluation/data/law_names_catalog_v1.json").read_bytes())
        self.assertEqual(419, len(_load_law_name_catalog(CATALOG)))

    def test_actual_loader_and_fresh_module_import_fail_on_invalid_catalog(self):
        base = json.loads(CATALOG.read_text())
        variants = [("valid", base), ("missing", None), ("json", b"{"), ("utf8", b"\xff")]
        for label in ["schema", "hash", "duplicate", "collision", "blank", "count", "source", "ordering"]:
            obj = copy.deepcopy(base)
            if label == "schema": obj["schema_version"] = 2
            elif label == "hash": obj["integrity"]["content_sha256"] = "0" * 64
            elif label == "duplicate": obj["law_names"][1] = obj["law_names"][0]
            elif label == "collision": obj["law_names"][-1] = "법인세 법"
            elif label == "blank": obj["law_names"][0] = " "
            elif label == "count": obj["name_count"] = 418
            elif label == "source": obj["source"]["database"] = "other"
            elif label == "ordering": obj["law_names"].reverse()
            variants.append((label, obj))
        for label, value in variants:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                (folder / "data").mkdir()
                (folder / "services").mkdir()
                target = folder / "data/law_names_catalog_v1.json"
                source = folder / "services/chunk_search_service.py"
                source.write_bytes(SOURCE.read_bytes())
                if value is not None:
                    target.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode())
                if label == "valid":
                    self.assertEqual(419, len(_load_law_name_catalog(target)))
                else:
                    with self.assertRaisesRegex(RuntimeError, "Cannot initialize canonical law catalog"):
                        _load_law_name_catalog(target)
                command = "import importlib.util,sys; s=importlib.util.spec_from_file_location('catalog_probe',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)"
                env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
                result = subprocess.run([sys.executable, "-c", command, str(source)], cwd=tmp, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(0 if label == "valid" else 1, result.returncode, result.stderr)
                if label != "valid": self.assertIn("Cannot initialize canonical law catalog", result.stderr)

    def test_prepared_catalog_reused_without_request_time_file_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "data").mkdir()
            (folder / "services").mkdir()
            source = folder / "services/chunk_search_service.py"
            source.write_bytes(SOURCE.read_bytes())
            target = folder / "data/law_names_catalog_v1.json"
            target.write_bytes(CATALOG.read_bytes())
            command = "import importlib.util,sys,pathlib; s=importlib.util.spec_from_file_location('catalog_probe',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); pathlib.Path(sys.argv[2]).unlink(); assert all(m.ChunkSearchService.extract_law_names('상속세및증여세법')==['상속세 및 증여세법'] for _ in range(50))"
            result = subprocess.run([sys.executable, "-c", command, str(source), str(target)], cwd=tmp, env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
