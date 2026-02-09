import os
import time
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from sari.core.indexer.worker import IndexWorker
from sari.core.models import IndexingResult
import pytest
from sari.core.db.main import LocalSearchDB
from sari.core.config.main import Config

@pytest.fixture
def mock_db():
    return LocalSearchDB(":memory:")

@pytest.fixture
def mock_cfg():
    cfg = MagicMock(spec=Config)
    cfg.workspace_roots = ["/tmp/test_root_matrix"]
    cfg.store_content = True
    return cfg

class TestConfigMatrix:
    # 1. Storage Matrix: STORE_CONTENT (True/False) + Compression
    def test_matrix_storage_compressed(self, mock_db, mock_cfg):
        from sari.core.settings import Settings
    
        # Scenario: Content Stored and Compressed
        with patch.dict("os.environ", {
            "SARI_STORE_CONTENT": "1",
            "SARI_STORE_CONTENT_COMPRESS": "1",
            "SARI_STORE_CONTENT_COMPRESS_LEVEL": "9"
        }):
            s = Settings()
            worker = IndexWorker(mock_cfg, mock_db, None, lambda p, c: ([], []), settings_obj=s)
    
            root = Path("/tmp/test_root_matrix")
            root.mkdir(parents=True, exist_ok=True)
            f = root / "hello.txt"
            content = "Hello " * 100
            f.write_text(content)
    
            res: IndexingResult = worker.process_file_task(root, f, f.stat(), int(time.time()), time.time(), False, root_id="root")
    
            assert res.type == "changed"
            assert isinstance(res.content, bytes)
            assert res.content.startswith(b"ZLIB\0")
            
            # Use a fixed, known path for storage verification
            test_path = "root/hello.txt"
            mock_db.upsert_root("root", str(root), str(root.resolve()), label="test")
            
            # Manually construct row with fixed path
            row = list(res.to_file_row())
            row[0] = test_path # Override path for strict match
            
            cur = mock_db._write.cursor()
            mock_db.upsert_files_tx(cur, [tuple(row)])
            mock_db._write.commit()
            
            # Verify restoration
            read_back = mock_db.read_file(test_path)
            assert read_back == content

    # 2. Search Policy Matrix: SEARCH_FIRST_MODE (warn vs enforce)
    def test_matrix_policy_enforcement(self):
        from sari.mcp.policies import PolicyEngine
        
        # Enforce Mode
        engine = PolicyEngine(mode="enforce")
        # Should block read without search
        res = engine.check_pre_call("read_file")
        assert res is not None
        # Check text in the response (URL encoded in PACK1)
        text = str(res)
        assert "search-first" in text.lower()
        
        # Mark search done
        engine.mark_action("search")
        res = engine.check_pre_call("read_file")
        assert res is None # Allowed now

    # 3. Filter Matrix: MAX_PARSE_BYTES
    def test_matrix_size_filtering(self, mock_db, mock_cfg):
        from sari.core.settings import Settings
        
        with patch.dict("os.environ", {
            "SARI_MAX_PARSE_BYTES": "5"
        }):
            s = Settings()
            worker = IndexWorker(mock_cfg, mock_db, None, lambda p, c: ([], []), settings_obj=s)
            
            root = Path("/tmp/test_root_size_matrix")
            root.mkdir(parents=True, exist_ok=True)
            f = root / "large.txt"
            f.write_text("1234567890")
            
            res: IndexingResult = worker.process_file_task(root, f, f.stat(), 0, 0, False)
            assert res.parse_status == "skipped"
            assert res.parse_reason == "too_large"

    def test_repo_label_prefers_git_top_level(self, mock_db, mock_cfg, tmp_path):
        from sari.core.settings import Settings
        s = Settings()
        worker = IndexWorker(mock_cfg, mock_db, None, lambda p, c: ([], []), settings_obj=s)

        root = tmp_path / "workspaceA"
        (root / "src").mkdir(parents=True, exist_ok=True)
        f = root / "src" / "app.py"
        f.write_text("print('x')")

        # Mocking sub-process result for Git top-level discovery
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=str(root / "real-repo"))
            res: IndexingResult = worker.process_file_task(root, f, f.stat(), int(time.time()), time.time(), False, root_id="root")
            assert res is not None
            assert res.repo == "real-repo"

    def test_repo_label_non_git_uses_first_directory(self, mock_db, mock_cfg, tmp_path):
        from sari.core.settings import Settings
        s = Settings()
        worker = IndexWorker(mock_cfg, mock_db, None, lambda p, c: ([], []), settings_obj=s)

        root = tmp_path / "workspaceB"
        (root / "services").mkdir(parents=True, exist_ok=True)
        f = root / "services" / "api.py"
        f.write_text("print('x')")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1) # Git fail
            res: IndexingResult = worker.process_file_task(root, f, f.stat(), int(time.time()), time.time(), False, root_id="root")
            assert res is not None
            # Was "services", but now we default to root name to avoid "src" being repo name
            assert res.repo == "workspaceB"

    def test_repo_label_root_file_uses_workspace_name(self, mock_db, mock_cfg, tmp_path):
        from sari.core.settings import Settings
        s = Settings()
        worker = IndexWorker(mock_cfg, mock_db, None, lambda p, c: ([], []), settings_obj=s)

        root = tmp_path / "workspaceC"
        root.mkdir(parents=True, exist_ok=True)
        f = root / "main.py"
        f.write_text("print('x')")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            res: IndexingResult = worker.process_file_task(root, f, f.stat(), int(time.time()), time.time(), False, root_id="root")
            assert res is not None
            assert res.repo == "workspaceC"