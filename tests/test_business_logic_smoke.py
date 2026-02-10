import shutil
from pathlib import Path
from sari.core.db.main import LocalSearchDB
from sari.core.indexer.main import Indexer
from sari.core.config import Config
from sari.core.workspace import WorkspaceManager


def test_core_business_logic_smoke():
    """
    End-to-end smoke test for the Ultra-Turbo architecture:
    Scanner -> Indexer -> DB -> Search -> Read.
    """
    # 1. Setup
    test_root = Path("/tmp/sari_smoke_test").resolve()
    if test_root.exists():
        shutil.rmtree(test_root)
    test_root.mkdir(parents=True)

    # Create sample files
    (test_root / "main.py").write_text("def hello():\n    pass")
    (test_root / "utils.js").write_text("function add(a, b) { return a + b; }")

    # 2. Initialize DB & Config
    db_path = test_root / "sari.db"
    db = LocalSearchDB(str(db_path))

    WorkspaceManager.root_id(str(test_root))
    # Note: Modern DB handles registration internally or via explicit call

    defaults = Config.get_defaults(str(test_root))
    cfg = Config(**defaults)

    # 3. Execution: Indexing (The Ultra-Turbo Way)
    indexer = Indexer(cfg, db)
    indexer.scan_once()

    # 4. Verification: End State
    assert db.read_file(str(test_root / "main.py")) is not None

    # 5. Search Verification (Using Hybrid Engine)
    # We use LocalSearchDB search methods directly
    files = db.search_files("hello")
    assert len(files) > 0
    assert "main.py" in files[0]["path"]
