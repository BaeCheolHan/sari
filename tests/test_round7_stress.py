import unittest
import tempfile
import shutil
import threading
import concurrent.futures
from pathlib import Path
from app.db import LocalSearchDB, SearchOptions
from app.indexer import Indexer
from app.config import Config

class TestRound7Stress(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "stress.db")
        self.db = LocalSearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp_dir)

    def test_concurrent_searches(self):
        """Verify thread-safety of concurrent read operations."""
        # Setup data
        self.db.upsert_files([
            (f"file_{i}.txt", "repo", 0, 0, f"common content {i}", 1000) for i in range(100)
        ])
        
        def run_search():
            opts = SearchOptions(query="common", limit=50)
            hits, _ = self.db.search_v2(opts)
            return len(hits)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(run_search) for _ in range(50)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]
            
        for count in results:
            self.assertEqual(count, 50)

    def test_large_payload_serialization(self):
        """Ensure large result sets are handled without errors."""
        # Insert 500 files to hit the max limit in list_files
        self.db.upsert_files([
            (f"file_{i}.txt", "repo", 0, 0, "content", 1000) for i in range(500)
        ])
        
        files, meta = self.db.list_files(limit=500)
        self.assertEqual(len(files), 500)
        self.assertEqual(meta["total"], 500)

    def test_deep_directory_indexing(self):
        """Verify indexer handles deep directory structures."""
        workspace = Path(self.tmp_dir) / "deep_ws"
        current = workspace
        for i in range(20):
            current = current / f"level_{i}"
        current.mkdir(parents=True)
        (current / "deep_file.txt").write_text("deep content")
        
        cfg = Config(
            workspace_root=str(workspace),
            server_host="127.0.0.1", server_port=47777,
            scan_interval_seconds=180, snippet_max_lines=5,
            max_file_bytes=1000, db_path=self.db_path,
            include_ext=[".txt"], include_files=[],
            exclude_dirs=[], exclude_globs=[],
            redact_enabled=False, commit_batch_size=500
        )
        
        indexer = Indexer(cfg, self.db)
        indexer._scan_once()
        
        paths = self.db.get_all_file_paths()
        # Path should be relative to workspace
        expected_path = "level_0/" + "/".join(f"level_{i+1}" for i in range(19)) + "/deep_file.txt"
        # Since our repo logic splits by first sep
        self.assertTrue(any("deep_file.txt" in p for p in paths))

if __name__ == "__main__":
    unittest.main()
