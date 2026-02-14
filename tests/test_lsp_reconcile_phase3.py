import time
from pathlib import Path

from sari.core.indexer.main import Indexer
from sari.core.models import IndexingResult
from sari.core.workspace import WorkspaceManager


def _seed_file(db, root: Path, rel_path: str, content: str, repo: str = "repo1") -> tuple[str, str]:
    rid = WorkspaceManager.root_id_for_workspace(str(root))
    db.upsert_root(rid, str(root), str(root))
    abs_path = root / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")

    row = IndexingResult(
        path=f"{rid}/{rel_path}",
        rel=rel_path,
        root_id=rid,
        repo=repo,
        type="changed",
        content=content,
        fts_content=content,
        mtime=int(time.time()),
        size=len(content),
        content_hash="h1",
        scan_ts=int(time.time()),
        metadata_json="{}",
    )
    db.upsert_files_turbo([row])
    db.finalize_turbo_batch()
    return rid, str(abs_path)


def test_db_lsp_dirty_candidates_and_clean(db, tmp_path):
    root = tmp_path / "ws"
    rid, _ = _seed_file(db, root, "src/a.py", "class A:\n    pass\n")
    db_path = f"{rid}/src/a.py"

    db.mark_lsp_dirty(db_path, root_id=rid, reason="unit-test")
    cands = db.get_lsp_dirty_candidates(limit=10)
    assert cands
    assert cands[0]["path"] == db_path

    db.mark_lsp_clean(db_path, error="")
    cands_after = db.get_lsp_dirty_candidates(limit=10)
    assert all(c["path"] != db_path for c in cands_after)


def test_indexer_reconcile_hydrates_symbols_and_cleans_dirty(db, tmp_path):
    root = tmp_path / "ws"
    rid, _ = _seed_file(db, root, "src/auth.py", "class AuthService:\n    def login(self):\n        return True\n")
    db_path = f"{rid}/src/auth.py"
    db.mark_lsp_dirty(db_path, root_id=rid, reason="startup")

    idx = Indexer.__new__(Indexer)
    idx.db = db
    idx.logger = None

    Indexer._reconcile_lsp_dirty_once(idx)

    # dirty should be cleared
    conn = db.get_connection()
    row = conn.execute("SELECT dirty FROM lsp_indexed_files WHERE path = ?", (db_path,)).fetchone()
    assert row is not None
    assert int(row[0]) == 0

    # reconcile metadata should be updated
    row2 = conn.execute(
        "SELECT dirty, row_version, updated_ts FROM lsp_indexed_files WHERE path = ?",
        (db_path,),
    ).fetchone()
    assert row2 is not None
    assert int(row2[0]) == 0
    assert int(row2[1]) > 0
    assert int(row2[2]) > 0

    sym_count = conn.execute(
        "SELECT COUNT(1) FROM lsp_symbols WHERE path = ?",
        (db_path,),
    ).fetchone()
    assert sym_count is not None
    assert int(sym_count[0]) >= 1
