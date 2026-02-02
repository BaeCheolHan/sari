#!/usr/bin/env python3
"""
Deckard Ranking & Policy Test Suite.
Consolidates 5 Cycles of Verification:
1. Core Edge Cases (Regex, Case, Filters)
2. Policy & Telemetry (Limits, Paging)
3. Hybrid Scoring (Definition, Priors, Proximity)
4. Recency & Noise (Time, Penalty)
5. E2E Scenarios (Real-world use cases)
"""
import os
import sys
import shutil
import tempfile
import unittest
import json
import time
import re
import sqlite3
from unittest.mock import patch
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import LocalSearchDB, SearchOptions
from mcp.tools.search import execute_search
from mcp.telemetry import TelemetryLogger
import threading

# Attempt to import for Cycle 11+
try:
    from app.indexer import _extract_symbols
except ImportError:
    _extract_symbols = None

# --- Mocks ---
class MockLogger(TelemetryLogger):
    def __init__(self):
        self.last_log = None
    def log_telemetry(self, msg):
        self.last_log = msg

# --- Test Classes ---

class TestRankingEdges(unittest.TestCase):
    """Cycle 1: Core Ranking Edge Cases"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_1.db")
        self.db = LocalSearchDB(self.db_path)
        
        files = [
            ("src/main.py", "def main(): pass", 100),
            ("src/utility.py", "# Helper", 50),
            ("src/User.java", "class User {}", 80),
            ("node_modules/pkg/index.js", "console.log('ignored')", 200),
            ("readme.md", "# Readme", 100),
        ]
        self.db.upsert_files([
            (f[0], "test_repo", 1000, f[2], f[1], 1000) for f in files
        ])

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_case_sensitivity(self):
        # 1. Insensitive (Default)
        opts = SearchOptions(query="user", limit=10, case_sensitive=False)
        hits, _ = self.db.search_v2(opts)
        self.assertTrue(any(h.path == "src/User.java" for h in hits))
        
        # 2. Sensitive
        opts = SearchOptions(query="user", limit=10, case_sensitive=True)
        hits, _ = self.db.search_v2(opts)
        self.assertFalse(any(h.path == "src/User.java" for h in hits))

    def test_regex_mode(self):
        opts = SearchOptions(query="class \w+", limit=10, use_regex=True)
        hits, meta = self.db.search_v2(opts)
        self.assertTrue(meta.get("regex_mode"))
        self.assertTrue(any(h.path == "src/User.java" for h in hits))

    def test_file_type_filter(self):
        opts = SearchOptions(query="main", limit=10, file_types=["py"])
        hits, _ = self.db.search_v2(opts)
        self.assertTrue(all(h.path.endswith(".py") for h in hits))

    def test_exclude_patterns(self):
        opts = SearchOptions(query="index", limit=10, exclude_patterns=["node_modules/*"])
        hits, _ = self.db.search_v2(opts)
        self.assertFalse(any("node_modules" in h.path for h in hits))

    def test_exact_filename_boost(self):
        opts = SearchOptions(query="main.py", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "src/main.py")


class TestRankingPolicy(unittest.TestCase):
    """Cycle 2: Policy & Telemetry"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_2.db")
        self.db = LocalSearchDB(self.db_path)
        self.logger = MockLogger()
        
        files = [(f"doc_{i}.txt", f"content {i}", 10) for i in range(30)]
        self.db.upsert_files([
            (f[0], "test_repo", 1000, f[2], f[1], 1000) for f in files
        ])

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_limit_cap(self):
        args = {"query": "content", "limit": 100}
        result = execute_search(args, self.db, self.logger)
        data = json.loads(result["content"][0]["text"])
        self.assertEqual(data["limit"], 20)
        self.assertEqual(len(data["results"]), 20)

    def test_default_limit(self):
        args = {"query": "content"}
        result = execute_search(args, self.db, self.logger)
        data = json.loads(result["content"][0]["text"])
        self.assertEqual(data["limit"], 8)

    def test_offset_paging(self):
        res1 = execute_search({"query": "content", "limit": 5, "offset": 0}, self.db, self.logger)
        ids1 = [r["path"] for r in json.loads(res1["content"][0]["text"])["results"]]
        
        res2 = execute_search({"query": "content", "limit": 5, "offset": 5}, self.db, self.logger)
        ids2 = [r["path"] for r in json.loads(res2["content"][0]["text"])["results"]]
        
        self.assertTrue(set(ids1).isdisjoint(set(ids2)))

    def test_warnings_in_meta(self):
        args = {"query": "content", "limit": 5}
        result = execute_search(args, self.db, self.logger)
        data = json.loads(result["content"][0]["text"])
        self.assertTrue(data["has_more"])
        self.assertIn("More results available", str(data["warnings"]))


class TestRankingHybrid(unittest.TestCase):
    """Cycle 3: Hybrid Scoring Logic"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_3.db")
        self.db = LocalSearchDB(self.db_path)
        
        def mk_file(path, content):
            return (path, "test_repo", 1000, len(content), content, 1000)

        data = []
        data.extend([
            mk_file("src/service.py", "class Service:\n    def run(self): pass"),
            mk_file("test/test_service.py", "service = Service()\nservice.run()"),
        ])
        data.extend([
            mk_file("src/config.json", '{"timeout": 100}'),
            mk_file("dist/config.json", '{"timeout": 100}'),
        ])
        data.extend([
            mk_file("notes.txt", "function logic"),
            mk_file("logic.py", "def logic(): pass"),
        ])
        data.extend([
            mk_file("close.md", "hello world"),
            mk_file("far.md", "hello ................. world"),
        ])
        data.extend([
            mk_file("src/check_user.py", ""),
            mk_file("src/user_check.py", ""),
        ])
        self.db.upsert_files(data)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_definition_boost(self):
        opts = SearchOptions(query="Service", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "src/service.py")

    def test_path_prior(self):
        opts = SearchOptions(query="timeout", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "src/config.json")

    def test_filetype_prior(self):
        opts = SearchOptions(query="logic", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "logic.py")

    def test_proximity_boost(self):
        opts = SearchOptions(query="hello world", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "close.md")

    def test_exact_suffix_boost(self):
        opts = SearchOptions(query="user_check", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "src/user_check.py")


class TestRankingRecency(unittest.TestCase):
    """Cycle 4: Recency & Noise Penalty"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_4.db")
        self.db = LocalSearchDB(self.db_path)
        
        def mk_file(path, content, mtime):
             return (path, "test_repo", mtime, len(content), content, 1000)

        now = int(time.time())
        old = now - 3600 * 24 * 30 
        
        data = []
        data.extend([
            mk_file("recent.py", "def common(): pass", now),
            mk_file("old.py",    "def common(): pass", old),
        ])
        data.extend([
            mk_file("app.min.js", "function x(){}", now),
            mk_file("app.js",     "function x(){}", now),
        ])
        data.extend([
            mk_file("package.json", "dependency", now),
            mk_file("yarn.lock",    "dependency", now),
        ])
        data.extend([
            mk_file("uniq.js.map", "mapping", now),
            mk_file("uniq.js",     "mapping", now),
        ])
        self.db.upsert_files(data)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_recency_basic(self):
        opts = SearchOptions(query="common", limit=5, recency_boost=True)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "recent.py")
        self.assertGreater(hits[0].score, hits[1].score)

    def test_recency_disabled(self):
        opts = SearchOptions(query="common", limit=5, recency_boost=False)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "recent.py")
        self.assertEqual(hits[0].score, hits[1].score)

    def test_noise_min_js(self):
        opts = SearchOptions(query="function", limit=5)
        hits, _ = self.db.search_v2(opts)
        paths = [h.path for h in hits if "app" in h.path]
        self.assertEqual(paths[0], "app.js")

    def test_noise_lock(self):
        opts = SearchOptions(query="dependency", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "package.json")

    def test_noise_map(self):
        opts = SearchOptions(query="mapping", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "uniq.js")


class TestRankingE2E(unittest.TestCase):
    """Cycle 5: End-to-End Scenarios"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_5.db")
        self.db = LocalSearchDB(self.db_path)
        
        def mk_file(repo, path, content):
            return (path, repo, 1000, len(content), content, 1000)
            
        data = []
        data.extend([
            mk_file("backend", "src/config.py", "class Config:\n    timeout = 10"),
            mk_file("backend", "config.json", '{"timeout": 10}'),
            mk_file("backend", "src/error.py", "class AppError(Exception): pass"),
            mk_file("backend", "logs/app.log", "Error occurred at..."),
        ])
        for i in range(5):
             data.append(mk_file(f"repo_{i}", f"README_{i}.md", "unique_term"))
        data.append(mk_file("target_repo", "docs/manual.md", "unique_term is here too"))
        
        data.append(mk_file("frontend", "src/User.tsx", "interface User {}"))
        data.append(mk_file("backend", "src/models/User.py", "class User(Model): pass"))
        
        self.db.upsert_files(data)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_scenario_config(self):
        opts = SearchOptions(query="timeout", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "src/config.py")

    def test_scenario_error(self):
        opts = SearchOptions(query="AppError", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "src/error.py")

    def test_scenario_repo_candidates(self):
        candidates = self.db.repo_candidates("unique_term", limit=3)
        self.assertTrue(len(candidates) > 0)

    def test_scenario_mixed_filters(self):
        opts = SearchOptions(query="User", repo="backend", file_types=["py"], limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, "src/models/User.py")

    def test_scenario_wildcard_filter(self):
        opts = SearchOptions(query="unique_term", limit=100, total_mode="exact")
        hits, meta = self.db.search_v2(opts)
        self.assertEqual(meta["total"], 6)


class TestRankingSnippet(unittest.TestCase):
    """Cycle 6: Smart Snippet Generation"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_6.db")
        self.db = LocalSearchDB(self.db_path)
        
        def mk_file(path, content):
            return (path, "test_repo", 1000, len(content), content, 1000)

        data = []
        data.append(mk_file("case.py", "class User:\n    pass"))
        content_best = (
            "user = User() # usage\n" + 
            ("\n" * 8) + 
            "class User: # definition\n" + 
            ("\n" * 5)
        )
        data.append(mk_file("best_match.py", content_best))
        content_density = (
            "A only here\n" +
            ("\n" * 8) +
            "A and B are here\n" +
            ("\n" * 5)
        )
        data.append(mk_file("density.txt", content_density))
        self.db.upsert_files(data)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_snippet_case_preservation(self):
        opts = SearchOptions(query="user", limit=5)
        hits, _ = self.db.search_v2(opts)
        snippet = hits[0].snippet
        self.assertIn(">>>User<<<", snippet)
        self.assertNotIn(">>>user<<<", snippet)

    def test_snippet_best_match_definition(self):
        opts = SearchOptions(query="User", limit=5, snippet_lines=3)
        hits, _ = self.db.search_v2(opts)
        snippet = hits[0].snippet
        self.assertIn("L10:", snippet)
        self.assertIn("class >>>User<<<", snippet)

    def test_snippet_density(self):
        opts = SearchOptions(query="A B", limit=5, snippet_lines=3)
        hits, _ = self.db.search_v2(opts)
        snippet = hits[0].snippet
        self.assertIn("L10:", snippet)

    def test_snippet_multiline_window(self):
        opts = SearchOptions(query="User", limit=5, snippet_lines=3)
        hits, _ = self.db.search_v2(opts)
        snippet = hits[0].snippet
        lines = snippet.strip().split("\n")
        self.assertTrue(1 <= len(lines) <= 3)

    def test_snippet_no_match(self):
        self.db.upsert_files([("nomatch.py", "test_repo", 1000, 10, "content empty", 1000)])
        opts = SearchOptions(query="nomatch", limit=5)
        hits, _ = self.db.search_v2(opts)
        snippet = hits[0].snippet
        self.assertIn("content empty", snippet)


class TestRankingInternational(unittest.TestCase):
    """Cycle 7: Korean & Special Characters"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_7.db")
        self.db = LocalSearchDB(self.db_path)
        
        def mk_file(path, content):
            return (path, "test_repo", 1000, len(content), content, 1000)

        data = []
        data.extend([
            mk_file("ko.txt", "사용자 설정 관리"),
            mk_file("mixed.txt", "class User 설정 파일"),
            mk_file("symbols.py", "def __init__(self):\n    pass"),
            mk_file("decorators.ts", "@Component\nclass App {}"),
            mk_file("dotted.py", "user.name = 'kim'"),
        ])
        self.db.upsert_files(data)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_korean_search(self):
        opts = SearchOptions(query="사용자", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, "ko.txt")

    def test_mixed_lang_search(self):
        opts = SearchOptions(query="User 설정", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "mixed.txt")

    def test_symbol_underscore(self):
        opts = SearchOptions(query="__init__", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertTrue(len(hits) > 0)
        self.assertEqual(hits[0].path, "symbols.py")

    def test_symbol_at(self):
        opts = SearchOptions(query="@Component", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "decorators.ts")

    def test_dotted_search(self):
        opts = SearchOptions(query="user.name", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(hits[0].path, "dotted.py")


class TestRankingPerformance(unittest.TestCase):
    """Cycle 8: Performance & Scale"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_8.db")
        self.db = LocalSearchDB(self.db_path)
        
    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_bulk_insert_search(self):
        data = []
        for i in range(1000):
            data.append((f"file_{i}.txt", "test_repo", 1000, 10, f"content term_{i}", 1000))
        
        self.db.upsert_files(data)
        
        start_search = time.time()
        opts = SearchOptions(query="term_500", limit=5)
        hits, _ = self.db.search_v2(opts)
        dur = time.time() - start_search
        
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, "file_500.txt")
        self.assertLess(dur, 0.2)

    def test_large_file_snippet(self):
        lines = [f"line {i}" for i in range(10000)]
        lines[5000] = "target match is here"
        content = "\n".join(lines)
        
        self.db.upsert_files([("large.txt", "repo", 1000, len(content), content, 1000)])
        
        start = time.time()
        opts = SearchOptions(query="target match", limit=5)
        hits, _ = self.db.search_v2(opts)
        dur = time.time() - start
        
        self.assertEqual(len(hits), 1)
        self.assertIn(">>>target<<<", hits[0].snippet)
        self.assertIn(">>>match<<<", hits[0].snippet)
        self.assertLess(dur, 0.3)

    def test_many_matches(self):
        content = "foo " * 1000
        self.db.upsert_files([("many.txt", "repo", 1000, len(content), content, 1000)])
        start = time.time()
        opts = SearchOptions(query="foo", limit=5)
        hits, _ = self.db.search_v2(opts)
        dur = time.time() - start
        self.assertLess(dur, 0.3)
        self.assertIn(">>>foo<<<", hits[0].snippet)

    def test_limit_optimization(self):
        self.db.upsert_files([
            (f"dummy_{i}.txt", "repo", 1000, 10, "common term", 1000) for i in range(50)
        ])
        opts = SearchOptions(query="common", limit=5)
        hits, meta = self.db.search_v2(opts)
        self.assertEqual(len(hits), 5)
        self.assertEqual(meta["total"], 50)

    def test_empty_result_speed(self):
        opts = SearchOptions(query="nonexistent_term_xyz", limit=5)
        start = time.time()
        hits, _ = self.db.search_v2(opts)
        dur = time.time() - start
        self.assertEqual(len(hits), 0)
        self.assertLess(dur, 0.1)


class TestRankingResilience(unittest.TestCase):
    """Cycle 9: Error Resilience"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_9.db")
        self.db = LocalSearchDB(self.db_path)
        self.db.upsert_files([("file.txt", "repo", 1000, 10, "content", 1000)])

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_bad_regex(self):
        opts = SearchOptions(query="[a-", limit=5, use_regex=True)
        try:
            hits, _ = self.db.search_v2(opts)
            self.assertEqual(len(hits), 0)
        except Exception as e:
            self.fail(f"Should not raise exception on bad regex: {e}")

    def test_very_long_query(self):
        long_q = "a" * 10000
        opts = SearchOptions(query=long_q, limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 0)

    def test_invalid_limit_types(self):
        opts = SearchOptions(query="content", limit='50')
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)

    def test_db_corruption_handling(self):
        self.db.close()
        opts = SearchOptions(query="content", limit=5)
        with self.assertRaises((sqlite3.ProgrammingError, sqlite3.OperationalError, AttributeError)):
            self.db.search_v2(opts)

    def test_null_query(self):
        opts = SearchOptions(query="", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 0)


class TestRankingFinal(unittest.TestCase):
    """Cycle 10: Final Integration"""
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_10.db")
        self.db = LocalSearchDB(self.db_path)
        self.db.upsert_files([
             ("src/main.py", "test_repo", 1000, 10, "content AA overlap", 1000),
             ("src/utils.py", "test_repo", 1000, 10, "utility", 1000)
        ])

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_ranking_determinism(self):
        opts = SearchOptions(query="content", limit=5)
        res1, _ = self.db.search_v2(opts)
        res2, _ = self.db.search_v2(opts)
        self.assertEqual(len(res1), len(res2))
        self.assertEqual(res1[0].path, res2[0].path)
        self.assertEqual(res1[0].score, res2[0].score)

    def test_snippet_highlight_overlaps(self):
        opts = SearchOptions(query="A", limit=5)
        hits, _ = self.db.search_v2(opts)
        snippet = hits[0].snippet
        self.assertIn(">>>A<<<", snippet)
        self.assertEqual(snippet.count(">>>A<<<"), 2)

    def test_path_normalization(self):
        opts = SearchOptions(query="UTILS.PY", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, "src/utils.py")

    def test_metadata_completeness(self):
        opts = SearchOptions(query="utility", limit=5)
        _, meta = self.db.search_v2(opts)
        self.assertIn("total", meta)
        self.assertTrue(meta["total"] >= 1)

    def test_empty_db_resilience(self):
        empty_dir = tempfile.mkdtemp()
        db_path = os.path.join(empty_dir, "empty.db")
        db = LocalSearchDB(db_path)
        opts = SearchOptions(query="anything", limit=5)
        hits, meta = db.search_v2(opts)
        self.assertEqual(len(hits), 0)
        self.assertEqual(meta["total"], 0)
        db.close()
        shutil.rmtree(empty_dir)




class TestCycle11(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_11.db")
        self.db = LocalSearchDB(self.db_path)
        
    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_extract_symbols_import(self):
        """Test 1: Verify _extract_symbols availability."""
        if _extract_symbols is None:
            self.fail("Could not import _extract_symbols from app.indexer")
        
        content = "class MyClass:\n    def my_method(self):\n        pass"
        symbols = _extract_symbols("src/test.py", content)
        names = [s[1] for s in symbols]
        self.assertIn("MyClass", names)
        self.assertIn("my_method", names)

    def test_symbol_manual_upsert(self):
        """Test 2: Manual Symbol Upsert & Search."""
        self.db.upsert_files([
            ("src/core.py", "repo", 1000, 10, "class Core:\n pass", 1000)
        ])
        
        symbols = [
            ("src/core.py", "Core", "class", 0, 1, "class Core:", "")
        ]
        self.db.upsert_symbols(symbols)
        
        opts = SearchOptions(query="Core", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, "src/core.py")
        # Boost is +500 if FTS matches too.
        self.assertGreater(hits[0].score, 400.0)

    def test_symbol_vs_text_match(self):
        """Test 3: Symbol (Definition) vs Text (Usage)."""
        self.db.upsert_files([
            ("src/caller.py", "repo", 1000, 10, "x = Core()", 1000)
        ])
        self.db.upsert_files([
            ("src/core.py", "repo", 1000, 10, "class Core:\n pass", 1000)
        ])
        self.db.upsert_symbols([
            ("src/core.py", "Core", "class", 0, 1, "class Core:", "")
        ])

        opts = SearchOptions(query="Core", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(hits[0].path, "src/core.py")
        self.assertGreater(hits[0].score, 400.0)
        
        if len(hits) > 1:
            self.assertEqual(hits[1].path, "src/caller.py")
            self.assertLess(hits[1].score, 400.0)

    def test_indexer_flow_simulation(self):
        """Test 4: Simulate Extract -> Upsert Flow."""
        content = "def calculate_sum(a, b):\n    return a + b"
        path = "src/math.py"
        
        self.db.upsert_files([(path, "repo", 1000, len(content), content, 1000)])
        
        symbols = _extract_symbols(path, content)
        self.assertEqual(len(symbols), 1)
        self.assertEqual(symbols[0][1], "calculate_sum")
        
        self.db.upsert_symbols(symbols)
        
        opts = SearchOptions(query="calculate_sum", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(hits[0].path, path)
        self.assertGreater(hits[0].score, 400.0)

    def test_symbol_merge_logic(self):
        """Test 5: Verify merged snippet Logic."""
        content = "import x\n\n\n\nclass Data:\n    pass" 
        path = "src/data.py"
        
        self.db.upsert_files([(path, "repo", 1000, len(content), content, 1000)])
        symbols = [(path, "Data", "class", 5, 6, "class Data:", "")]
        self.db.upsert_symbols(symbols)
        
        opts = SearchOptions(query="Data", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        snippet = hits[0].snippet
        self.assertIn("class Data", snippet)

class TestCycle12(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_12.db")
        self.db = LocalSearchDB(self.db_path)
        
    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_multisymbol_file(self):
        """Test 1: Multiple symbols in one file."""
        # Insert 10 symbols: FuncA, FuncB, ...
        symbols = []
        path = "src/funcs.py"
        content = ""
        for i in range(10):
            name = f"Func{i}"
            symbols.append((path, name, "def", i, i+1, f"def {name}(): pass", ""))
            content += f"def {name}(): pass\n"
            
        self.db.upsert_files([(path, "repo", 1000, len(content), content, 1000)])
        self.db.upsert_symbols(symbols)
        
        # Search for Func5
        opts = SearchOptions(query="Func5", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, path)
        self.assertGreater(hits[0].score, 400.0)

    def test_dupe_symbol_name(self):
        """Test 2: Same Symbol name in different files."""
        # class Generic in two files
        self.db.upsert_files([
            ("src/a.py", "repo", 1000, 10, "class Generic: pass", 1000),
            ("src/b.py", "repo", 1000, 10, "class Generic: pass", 1000)
        ])
        self.db.upsert_symbols([
            ("src/a.py", "Generic", "class", 0, 1, "class Generic:", ""),
            ("src/b.py", "Generic", "class", 0, 1, "class Generic:", "")
        ])
        
        opts = SearchOptions(query="Generic", limit=10)
        hits, _ = self.db.search_v2(opts)
        
        # Both should match with high score
        self.assertEqual(len(hits), 2)
        self.assertGreater(hits[0].score, 400.0)
        self.assertGreater(hits[1].score, 400.0)

    def test_symbol_update_refreshes(self):
        """Test 3: Updating file symbols correctly clears old ones."""
        path = "src/dynamic.py"
        # 1. State A: class Old
        self.db.upsert_files([(path, "repo", 1000, 10, "class Old: pass", 1000)])
        self.db.upsert_symbols([(path, "Old", "class", 0, 1, "class Old:", "")])
        
        # Search Old -> Hit
        opts = SearchOptions(query="Old", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)
        
        # 2. State B: class New (Update) & Old removed
        self.db.upsert_files([(path, "repo", 2000, 10, "class New: pass", 2000)])
        # upsert_symbols should replace content for this path
        self.db.upsert_symbols([(path, "New", "class", 0, 1, "class New:", "")])
        
        # Search Old -> Should NOT find as Symbol (maybe text FTS finds it if content not synced? but we updated content too)
        # Content updated to "class New...". "Old" is gone.
        # So "Old" check should return 0 results.
        opts = SearchOptions(query="Old", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 0)
        
        # Search New -> Hit (Symbol Boost)
        opts = SearchOptions(query="New", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)
        self.assertGreater(hits[0].score, 400.0)

    def test_nested_symbols(self):
        """Test 4: Nested Symbols (Parent/Child)."""
        # Not strictly hierarchical in DB, but both should be indexed.
        path = "src/nest.py"
        symbols = [
            (path, "Outer", "class", 0, 5, "class Outer:", ""),
            (path, "Inner", "class", 1, 4, "  class Inner:", "Outer")
        ]
        content = "class Outer:\n  class Inner:\n    pass"
        self.db.upsert_files([(path, "repo", 1000, len(content), content, 1000)])
        self.db.upsert_symbols(symbols)
        
        # Search Inner
        opts = SearchOptions(query="Inner", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)
        self.assertGreater(hits[0].score, 400.0)

    def test_delete_file_clears_symbols(self):
        """Test 5: Check Cascading Deletion (Orphan check)."""
        path = "src/orphan.py"
        self.db.upsert_files([(path, "repo", 1000, 10, "class Orphan:", 1000)])
        self.db.upsert_symbols([(path, "Orphan", "class", 0, 1, "class Orphan:", "")])
        
        # Confirm existence
        opts = SearchOptions(query="Orphan", limit=5)
        self.assertEqual(len(self.db.search_v2(opts)[0]), 1)
        
        # Delete file via delete_unseen_files
        # We simulate "scan_ts" = 2000. Our file has last_seen = 1000 (from upsert).
        # So it is "unseen".
        self.db.delete_unseen_files(2000)
        
        # Search Orphan -> Should be gone (both FTS and Symbol)
        # If FTS gone but Symbol remains -> Partial Bug.
        # If Symbol remains -> it might match? 
        # Search_v2 joins symbols on path?
        # If FTS matched file is gone, Search_v2 relies on Symbol HIT?
        # Symbol HIT has `path`.
        # If file is gone from `files`, can we still return result?
        # `search_v2` checks `files` table for content?
        # "SELECT ... FROM files_fts JOIN files ..."
        # BUT Symbol Search is: "SELECT ... FROM symbols ..."
        # If Symbol table still has it, we might return it?
        # BUT we merge with `files` row.
        # If file missing, do we crash or skip?
        # Code: `merged_map[sh.path] = sh`
        # Then we return list(merged_map.values()).
        # We assume `sh` (SearchHit) is constructed from Symbol.
        # SearchHit has `content` field.
        # Symbol table stores `content` (snippet).
        # So we MIGHT return a "Ghost" symbol if not deleted!
        
        # Let's verify what happens. Ideally it should be deleted.
        # If not, we found a bug to fix (Add FK Cascade).
        
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 0)

class TestCycle13(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_13.db")
        self.db = LocalSearchDB(self.db_path)
        
    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_unicode_symbols(self):
        """Test 1: Unicode Symbol Names."""
        path = "src/ko.py"
        content = "class 유저:\n    pass"
        self.db.upsert_files([(path, "repo", 1000, len(content), content, 1000)])
        self.db.upsert_symbols([(path, "유저", "class", 0, 1, "class 유저:", "")])
        
        opts = SearchOptions(query="유저", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, path)
        self.assertGreater(hits[0].score, 400.0)

    def test_long_symbol_name(self):
        """Test 2: Extremely Long Symbol Name."""
        name = "VeryLong" * 50
        path = "src/long.py"
        content = f"class {name}: pass"
        self.db.upsert_files([(path, "repo", 1000, len(content), content, 1000)])
        self.db.upsert_symbols([(path, name, "class", 0, 1, f"class {name}:", "")])
        
        opts = SearchOptions(query=name[:20], limit=5)
        hits, _ = self.db.search_v2(opts)
        
        # It should match partially or exactly if query matches FTS logic
        # Here we query prefix.
        # FTS might not match substring unless we use wildcard.
        # BUT `search_v2` logic tries to match symbol name.
        # `search_symbols` uses `name MATCH ?` or `name LIKE ?`?
        # Likely `search_symbols` logic is FTS on symbols table?
        # Let's try exact match query.
        
        opts = SearchOptions(query=name, limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)

    def test_symbol_line_mismatch(self):
        """Test 3: Symbol points to non-existent line."""
        path = "src/short.py"
        content = "line 1\nline 2" # 2 lines
        self.db.upsert_files([(path, "repo", 1000, len(content), content, 1000)])
        # Symbol points to line 100
        self.db.upsert_symbols([(path, "Bug", "class", 100, 101, "class Bug:", "")])
        
        # Search "Bug" -> Should hit Symbol
        # Snippet generation might fail if it tries to read line 100 from file content?
        # OR it uses `content` stored in Symbol table?
        # `search_v2` uses `sh.snippet`. `sh.snippet` comes from `row["content"]` in `search_symbols`.
        # So it uses the CACHED content in symbol table, NOT file content.
        # So it should be fine!
        
        opts = SearchOptions(query="Bug", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(len(hits), 1)
        self.assertIn("class Bug", hits[0].snippet) 

    def test_empty_symbol_content(self):
        """Test 4: Symbol with empty content string."""
        path = "src/empty.py"
        self.db.upsert_files([(path, "repo", 1000, 10, "class Empty:", 1000)])
        self.db.upsert_symbols([(path, "Empty", "class", 0, 1, "", "")])
        
        opts = SearchOptions(query="Empty", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(len(hits), 1)
        # Symbol content is empty, so logic falls back to FTS snippet or keeps it.
        # FTS found "class Empty".
        self.assertIn("class >>>Empty<<<", hits[0].snippet)

    def test_special_char_symbols(self):
        """Test 5: Symbols with special characters."""
        path = "src/ops.cpp"
        name = "operator<<"
        content = "void operator<<() {}"
        self.db.upsert_files([(path, "repo", 1000, len(content), content, 1000)])
        self.db.upsert_symbols([(path, name, "function", 0, 1, content, "")])
        
        # FTS5 special chars might need quoting.
        # If I search "operator<<", typically tokenized as "operator".
        # If I search "operator", it should find it.
        opts = SearchOptions(query="operator", limit=5)
        hits, _ = self.db.search_v2(opts)
        self.assertEqual(len(hits), 1)
        self.assertGreater(hits[0].score, 400.0)

class TestCycle14(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_14.db")
        self.db = LocalSearchDB(self.db_path)
        
    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_symbol_repo_filter(self):
        """Test 1: Repo filter applies to symbols (or result filtering)."""
        # Symbols table has no 'repo' column?
        # Let's check schema. `view_code_item` showed upsert_symbols columns.
        # It did NOT show `repo`.
        # `search_symbols` query?
        # If symbols table lacks repo, how do we filter?
        # `search_v2` logic:
        # `search_symbols(query) -> hits`. Then it merges.
        # Then `_search_fts(repo=...) -> hits`
        # If symbol hit corresponds to a path in a repo that is filtered out...
        # Does `search_symbols` join with `files` to check repo?
        # If not, we might be leaking symbols from other repos!
        # This is CRITICAL verification.
        
        # Setup: Two files, same path relative? No, path is usually absolute or relative to root.
        # But `repo` is a metadata field in `files` table. `path` is PK in `files`.
        # `symbols` links to `files` via `path`.
        
        # Scenario:
        # File 1: path="backend/User.py", repo="backend"
        # File 2: path="frontend/User.ts", repo="frontend"
        
        self.db.upsert_files([
            ("backend/User.py", "backend", 1000, 10, "class User:", 1000),
            ("frontend/User.ts", "frontend", 1000, 10, "class User {}", 1000)
        ])
        self.db.upsert_symbols([
            ("backend/User.py", "User", "class", 0, 1, "class User:", ""),
            ("frontend/User.ts", "User", "class", 0, 1, "class User {}", "")
        ])
        
        # Search "User", repo="backend"
        # If logic is correct, frontend should disappear.
        opts = SearchOptions(query="User", repo="backend", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        # NOTE: If search_symbols doesn't filter by repo, we might get frontend hit?
        # BUT search_v2 merges ... 
        # Actually search_v2 currently might NOT filter symbols by repo if they are not FTS hits!
        # If FTS returns 0 for backend (e.g. if we only search symbols?), 
        # or if we merge...
        # Let's see if there is a bug here.
        
        paths = [h.path for h in hits]
        # Expectation: Only backend/User.py
        self.assertIn("backend/User.py", paths)
        self.assertNotIn("frontend/User.ts", paths)

    def test_symbol_filetype_prior(self):
        """Test 2: Java vs Text prioritization."""
        self.db.upsert_files([
            ("src/Main.java", "repo", 1000, 10, "class Main {}", 1000),
            ("doc/main.txt", "repo", 1000, 10, "Usage: Main", 1000)
        ])
        self.db.upsert_symbols([
            ("src/Main.java", "Main", "class", 0, 1, "class Main {}", "")
        ])
        
        opts = SearchOptions(query="Main", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        # Main.java should win (Symbol 400+ vs Text < 100)
        self.assertEqual(hits[0].path, "src/Main.java")
        self.assertGreater(hits[0].score, hits[1].score)

    def test_symbol_limit_handling(self):
        """Test 3: Limits with many symbols."""
        # Insert 20 symbols
        symbols = []
        files = []
        for i in range(20):
            p = f"file_{i}.py"
            s = f"Sym{i}"
            files.append((p, "repo", 1000, 10, f"class {s}:", 1000))
            symbols.append((p, s, "class", 0, 1, f"class {s}:", ""))
            
        self.db.upsert_files(files)
        self.db.upsert_symbols(symbols)
        
        # Query "Sym" -> should match all if tokenized (Sym0, Sym1...)
        # But "Sym" might not match "Sym0" in FTS exact?
        # Symbols "name LIKE %query%"?
        # If I search "Sym", strict string match might fail.
        # I'll search "Sym" and make symbols all named "Sym" but in different files?
        # Or search "Sym0" to "Sym4" specifically?
        # Let's search "Sym" and ensure "Sym" matches "SymStart..." if prefix?
        # Or rename them all "Target".
        
        symbols = []
        files = []
        for i in range(20):
            p = f"target_{i}.py"
            name = "Target"
            files.append((p, "repo", 1000, 10, f"class {name}:", 1000))
            symbols.append((p, name, "class", 0, 1, f"class {name}:", ""))
        self.db.upsert_files(files)
        self.db.upsert_symbols(symbols)
            
        opts = SearchOptions(query="Target", limit=10)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(len(hits), 10) # Capped at limit

    def test_symbol_exact_match_interaction(self):
        """Test 4: Merged Score Interaction."""
        # "User" vs "UserFactory"
        self.db.upsert_files([
            ("src/User.py", "repo", 1000, 10, "class User:", 1000),
            ("src/UserFactory.py", "repo", 1000, 10, "class UserFactory:", 1000)
        ])
        self.db.upsert_symbols([
            ("src/User.py", "User", "class", 0, 1, "class User:", ""),
            ("src/UserFactory.py", "UserFactory", "class", 0, 1, "class UserFactory:", "")
        ]) # UserFactory doesn't match "User" exactly in symbol lookup unless partial?
        # But FTS will find "User" in "UserFactory" (token).
        
        # If I search "User":
        # Symbol "User" matches "User" -> Hit (1000)
        # Symbol "UserFactory" matches "User"? If LIKE %User%?
        # Let's assume matches.
        
        # FTS "User.py" -> Exact match boost (+2)
        # FTS "UserFactory.py" -> Partial match boost (+1)
        
        opts = SearchOptions(query="User", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(hits[0].path, "src/User.py")

    def test_symbol_not_found(self):
        """Test 5: Fallback to FTS."""
        self.db.upsert_files([("src/foo.py", "repo", 1000, 10, "content bar", 1000)])
        # No symbol
        
        opts = SearchOptions(query="bar", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertEqual(len(hits), 1)
        self.assertLess(hits[0].score, 100.0)

class TestCycle15(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cycle_15.db")
        self.db = LocalSearchDB(self.db_path)
        
    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_perf_10k_symbols(self):
        """Test 1: 10,000 Symbols Upsert & Search Latency."""
        # Generate 1000 files, 10 symbols each
        files = []
        symbols = []
        for i in range(1000):
            path = f"file_{i}.py"
            content = "content"
            files.append((path, "repo", 1000, 10, content, 1000))
            for j in range(10):
                sname = f"Sym_{i}_{j}"
                symbols.append((path, sname, "def", 0, 1, f"def {sname}", ""))
        
        start = time.time()
        self.db.upsert_files(files)
        self.db.upsert_symbols(symbols)
        # print(f"Inserted 10k symbols in {time.time() - start:.3f}s")
        
        start_search = time.time()
        opts = SearchOptions(query="Sym_500_5", limit=5)
        hits, _ = self.db.search_v2(opts)
        dur = time.time() - start_search
        
        self.assertEqual(len(hits), 1)
        self.assertLess(dur, 0.2, "Symbol search latency too high")

    def test_concurrent_ops(self):
        """Test 2: Concurrent Upsert & Search."""
        # Thread 1: Upserts
        # Thread 2: Searches
        
        stop_event = threading.Event()
        errors = []
        
        def writer():
            try:
                for i in range(50):
                    if stop_event.is_set(): break
                    path = f"thread_{i}.py"
                    self.db.upsert_files([(path, "repo", 1000, 10, "class Thread:", 1000)])
                    self.db.upsert_symbols([(path, "Thread", "class", 0, 1, "class Thread:", "")])
                    time.sleep(0.005)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(50):
                    if stop_event.is_set(): break
                    opts = SearchOptions(query="Thread", limit=5)
                    self.db.search_v2(opts)
                    time.sleep(0.005)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        
        t1.start()
        t2.start()
        
        t1.join(timeout=2)
        stop_event.set()
        t2.join(timeout=2)
        
        if errors:
            self.fail(f"Concurrent errors: {errors}")

    def test_symbol_wildcard(self):
        """Test 3: Prefix/Wildcard search compatibility."""
        path = "src/wild.py"
        self.db.upsert_files([(path, "repo", 1000, 10, "class WildCard:", 1000)])
        self.db.upsert_symbols([(path, "WildCard", "class", 0, 1, "class WildCard:", "")])
        
        # Search "Wild" -> Matches "WildCard" prefix?
        # Depends on Search v2 logic.
        # If implemented as prefix search (LIKE 'Wild%') it works.
        opts = SearchOptions(query="Wild", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        # If Wild matches via FTS, we get it.
        # If Wild matches via Symbol (WildCard), we get it.
        self.assertEqual(len(hits), 1)

    def test_hit_reason_metadata(self):
        """Test 4: Verify Reason includes Symbol."""
        path = "src/reason.py"
        self.db.upsert_files([(path, "repo", 1000, 10, "class Reason:", 1000)])
        self.db.upsert_symbols([(path, "Reason", "class", 0, 1, "class Reason:", "")])
        
        opts = SearchOptions(query="Reason", limit=5)
        hits, _ = self.db.search_v2(opts)
        
        self.assertTrue(hasattr(hits[0], "hit_reason"))
        # Should contain "Symbol match" or similar?
        # Checking db.py: `sh.hit_reason = f"Symbol: {name}"` in search_symbols
        # merged: `f"{sh.hit_reason}, {existing.hit_reason}"`
        # Expect "Symbol: Reason"
        self.assertIn("Symbol:", hits[0].hit_reason)

    def test_full_system_refresh(self):
        """Test 5: Clear and Reload."""
        path = "src/refresh.py"
        self.db.upsert_files([(path, "repo", 1000, 10, "class Ref:", 1000)])
        self.db.upsert_symbols([(path, "Ref", "class", 0, 1, "class Ref:", "")])
        
        hits, _ = self.db.search_v2(SearchOptions(query="Ref"))
        self.assertEqual(len(hits), 1)
        
        # "Clear" via upsert empty? No, delete_unseen.
        # Mark everything unseen.
        self.db.delete_unseen_files(2000) # ts=1000 is unseen
        
        hits, _ = self.db.search_v2(SearchOptions(query="Ref"))
        self.assertEqual(len(hits), 0)
        
        # Re-insert
        self.db.upsert_files([(path, "repo", 3000, 10, "class Ref:", 3000)])
        self.db.upsert_symbols([(path, "Ref", "class", 0, 1, "class Ref:", "")])
        
        hits, _ = self.db.search_v2(SearchOptions(query="Ref"))
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()
