from sari.core.db.main import LocalSearchDB
from sari.core.db.storage import GlobalStorageManager

def test_storage_direct_turbo_persistence(tmp_path):
    """
    Verify that the storage backend correctly persists data through the Turbo path.
    """
    db_path = tmp_path / "storage.db"
    db = LocalSearchDB(str(db_path))
    db.ensure_root("root", str(tmp_path))

    # 1. Prepare bulk data
    rows = []
    for i in range(100):
        rows.append((f"p{i}", f"rel{i}", "root", "repo", 0, 10, b"data", "h", "fts", 0, 0, "ok", "", "ok", "", 0, 0, 0, 10, "{}"))

    # 2. Bulk Ingestion
    db.upsert_files_turbo(rows)
    db.finalize_turbo_batch()

    # 3. Persistence Check
    assert len(db.search_files("rel50")) == 1
    db.close()

    # 4. Reload Check (Real durability)
    db2 = LocalSearchDB(str(db_path))
    assert len(db2.search_files("rel99")) == 1


def test_storage_overlay_mtime_respects_newer_rows():
    class DummyDB:
        db_path = ":memory:"

    storage = GlobalStorageManager(DummyDB())

    path = "root/rel/path.py"
    row_new = (path, "rel/path.py", "root", "repo", 100, 10, b"", "h", "snippet-new")
    row_old = (path, "rel/path.py", "root", "repo", 90, 10, b"", "h", "snippet-old")
    row_newer = (path, "rel/path.py", "root", "repo", 110, 10, b"", "h", "snippet-newer")

    storage.upsert_files([row_new])
    storage.upsert_files([row_old])
    assert storage._overlay_files[path][3] == 100
    assert storage._overlay_files[path][5] == "snippet-new"

    storage.upsert_files([row_newer])
    assert storage._overlay_files[path][3] == 110
    assert storage._overlay_files[path][5] == "snippet-newer"
