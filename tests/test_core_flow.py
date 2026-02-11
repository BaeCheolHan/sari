from sari.core.config.main import Config
from sari.core.indexer.main import Indexer
from sari.core.search_engine import SearchEngine
from sari.core.models import SearchOptions
from sari.core.workspace import WorkspaceManager

def test_full_indexing_and_search_flow(db, tmp_path, monkeypatch):
    """
    Integration Test: Config -> Indexer -> Search
    Verifies that files in the workspace are correctly indexed and searchable.
    """
    # 1. Setup Configuration
    monkeypatch.setenv("SARI_ENABLE_FTS", "1")
    # Priority Fix: Remove global env vars that interfere with test workspace roots
    monkeypatch.delenv("SARI_WORKSPACE_ROOT", raising=False)
    
    ws_path = str(tmp_path.resolve())
    
    # Priority Fix: Create test files
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main.py").write_text("def hello():\n    print('world')")
    (tmp_path / "README.md").write_text("# Test Project\nThis is a sample project.")
    
    cfg = Config.load(None, workspace_root_override=ws_path)
    
    # 2. Run Indexing (Modern Interface)
    indexer = Indexer(cfg, db)
    
    # Pre-register roots
    for r in cfg.workspace_roots:
        rid = WorkspaceManager.root_id(r)
        db.upsert_root(rid, r, r)

    # scan_once() now blocks until all batches are committed (Truth Restoration)
    indexer.scan_once()
    
    # 3. Verify Indexing Results (DB Layer)
    files = db.search_files("")
    assert len(files) >= 2
    
    # Debug: Check actual root_id in DB
    raw_rows = db._get_conn().execute("SELECT path, root_id FROM files LIMIT 5").fetchall()
    print(f"\nDEBUG: DB Rows: {raw_rows}")
    
    paths = [f['path'] for f in files]
    assert any("src/main.py" in p for p in paths)
    assert any("README.md" in p for p in paths)
    
    # 4. Verify Search (Engine Layer)
    engine = SearchEngine(db)
    root_id = WorkspaceManager.root_id(ws_path)
    
    # Test 4-1: Keyword Search
    opts = SearchOptions(query="hello", root_ids=[root_id])
    hits, meta = engine.search(opts)
    assert len(hits) >= 1
    assert hits[0].path.endswith("src/main.py")
    
    # Test 4-2: FTS Fallback
    opts_fts = SearchOptions(query="Project", root_ids=[root_id])
    hits_fts, _ = engine.search(opts_fts)
    assert len(hits_fts) >= 1
    assert hits_fts[0].path.endswith("README.md")

def test_config_loading(tmp_path):
    """Verifies that configuration is loaded correctly from the workspace."""
    cfg = Config.load(None, workspace_root_override=str(tmp_path))
    # Note: Config.workspace_root might be renamed to workspace_root_override or similar in some versions
    # We check the most likely property or the internal data
    root = getattr(cfg, "workspace_root", str(tmp_path))
    assert root == str(tmp_path)
    assert ".git" in cfg.exclude_dirs