import sqlite3
from types import SimpleNamespace

from sari.core.db.storage import GlobalStorageManager
from sari.core.repository.file_repository import FileRepository


class _FakeWriter:
    def __init__(self, db, logger=None, max_batch=0, max_wait=0, on_commit=None):
        self.db = db
        self.logger = logger
        self.max_batch = max_batch
        self.max_wait = max_wait
        self.on_commit = on_commit
        self.started = False
        self.enqueued = []
        self._qsize = 0
        self.flush_ret = True
        self.stop_ret = True

    def start(self):
        self.started = True

    def stop(self):
        return self.stop_ret

    def enqueue(self, task):
        self.enqueued.append(task)

    def qsize(self):
        return self._qsize

    def flush(self):
        return self.flush_ret


def _mk_db(path):
    return SimpleNamespace(db_path=path)


def test_storage_get_instance_handles_switch_fail_and_success(monkeypatch):
    import sari.core.db.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DBWriter", _FakeWriter)
    GlobalStorageManager._instance = None

    old = SimpleNamespace(db=_mk_db("/tmp/old.db"))

    def _boom():
        raise RuntimeError("shutdown fail")

    old.shutdown = _boom
    GlobalStorageManager._instance = old
    got = GlobalStorageManager.get_instance(_mk_db("/tmp/new.db"))
    assert got is old
    assert GlobalStorageManager._last_switch_block_reason

    old2 = SimpleNamespace(db=_mk_db("/tmp/old2.db"))
    old2.shutdown = lambda: True
    GlobalStorageManager._instance = old2
    got2 = GlobalStorageManager.get_instance(_mk_db("/tmp/new2.db"))
    assert got2 is not old2
    assert got2.db.db_path == "/tmp/new2.db"
    assert got2.writer.started is True

    GlobalStorageManager._instance = None


def test_storage_get_instance_without_db_uses_global_path(monkeypatch, tmp_path):
    import sari.core.db.storage as storage_mod
    from sari.core.workspace import WorkspaceManager
    from sari.core.db import main as db_main

    monkeypatch.setattr(storage_mod, "DBWriter", _FakeWriter)
    monkeypatch.setattr(WorkspaceManager, "get_global_db_path", lambda: tmp_path / "global.db")
    monkeypatch.setattr(db_main, "LocalSearchDB", lambda p: _mk_db(str(p)))

    GlobalStorageManager._instance = None
    inst = GlobalStorageManager.get_instance()
    assert inst.db.db_path.endswith("global.db")
    assert inst.writer.started is True
    GlobalStorageManager._instance = None


def test_storage_overlay_commit_delete_queue_and_recent_search(monkeypatch):
    import sari.core.db.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DBWriter", _FakeWriter)
    m = GlobalStorageManager(_mk_db(":memory:"))
    m._max_overlay_size = 1

    row1 = ("rid/a.py", "a.py", "rid", "repo1", 100, 10, b"", "h", "Hello World")
    row2 = ("rid/b.py", "b.py", "rid", "repo1", 110, 20, b"", "h", "Another")
    m.upsert_files([row1, row2], engine_docs=[{"id": "rid/a.py"}, {"id": "rid/b.py"}, {"id": "x"}])
    assert len(m._overlay_files) == 1
    assert "rid/b.py" in m._overlay_files
    assert len(m.writer.enqueued) == 1
    upsert_task = m.writer.enqueued[0]
    assert upsert_task.kind == "upsert_files"
    assert len(upsert_task.engine_docs) == 2

    m._overlay_files["rid/c.py"] = {
        "path": "rid/c.py",
        "root_id": "rid2",
        "repo": "repo2",
        "mtime": 90,
        "size": 1,
        "snippet": "needle",
    }
    got = m.get_recent_files("needle", root_id="rid2", limit=1)
    assert len(got) == 1
    assert got[0]["path"] == "rid/c.py"

    m._on_db_commit(["rid/c.py"])
    assert "rid/c.py" not in m._overlay_files

    m.delete_file("rid/b.py", engine_deletes=["rid/b.py"])
    assert m.writer.enqueued[-1].kind == "delete_path"
    m.enqueue_task(SimpleNamespace(kind="noop"))
    assert m.writer.enqueued[-1].kind == "noop"

    m.writer._qsize = 10000
    assert m.get_queue_load() == 1.0


def test_storage_shutdown_incomplete_and_complete(monkeypatch):
    import sari.core.db.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DBWriter", _FakeWriter)
    m = GlobalStorageManager(_mk_db(":memory:"))
    m.writer.flush_ret = False
    m.writer.stop_ret = True
    assert m.shutdown() is False

    m.writer.flush_ret = True
    m.writer.stop_ret = True
    assert m.shutdown() is True


def _make_repo():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE files(
            path TEXT PRIMARY KEY,
            rel_path TEXT,
            root_id TEXT,
            repo TEXT,
            mtime INTEGER,
            size INTEGER,
            content BLOB,
            hash TEXT,
            fts_content TEXT,
            last_seen_ts INTEGER,
            deleted_ts INTEGER,
            status TEXT,
            error TEXT,
            parse_status TEXT,
            parse_error TEXT,
            ast_status TEXT,
            ast_reason TEXT,
            is_binary INTEGER,
            is_minified INTEGER,
            metadata_json TEXT
        )
        """
    )
    cur.execute("CREATE TABLE symbols(path TEXT)")
    cur.execute("CREATE TABLE symbol_relations(from_path TEXT, to_path TEXT)")
    conn.commit()
    return FileRepository(conn), conn


def test_file_repository_upsert_update_and_query_paths():
    repo, conn = _make_repo()
    cur = conn.cursor()
    cur.execute("INSERT INTO symbols(path) VALUES('p1')")
    conn.commit()

    rows = [
        ("",),  # skipped because no path
        ("p1", "rel1", "root1", "repo1", 100, 10, b"abc", "h1", "fts1", 90, 0, "ok", "", "ok", "", "none", "none", 0, 0, '{"content_hash":"c1"}'),
    ]
    n = repo.upsert_files_tx(cur, rows)
    conn.commit()
    assert n == 1

    file_row = conn.execute("SELECT * FROM files WHERE path='p1'").fetchone()
    assert file_row is not None
    assert file_row["repo"] == "repo1"
    sym_row = conn.execute("SELECT * FROM symbols WHERE path='p1'").fetchall()
    assert sym_row == []

    # stale update should not overwrite due to mtime guard
    repo.upsert_files_tx(cur, [("p1", "rel1", "root1", "repo2", 90, 10, b"zzz")])
    conn.commit()
    same = conn.execute("SELECT repo,mtime FROM files WHERE path='p1'").fetchone()
    assert same["repo"] == "repo1"
    assert same["mtime"] == 100

    assert repo.get_file_meta("p1") == (100, 10, "c1")
    assert repo.get_file_meta("nope") is None

    conn.execute("UPDATE files SET metadata_json='{bad' WHERE path='p1'")
    conn.commit()
    assert repo.get_file_meta("p1") is None

    conn.execute(
        "INSERT INTO files(path, rel_path, root_id, repo, mtime, size, content, hash, fts_content, last_seen_ts, deleted_ts, status, error, parse_status, parse_error, ast_status, ast_reason, is_binary, is_minified, metadata_json)"
        " VALUES('p2','rel2','root2','repo2',120,20,x'01','h2','fts2',50,0,'ok','','ok','','none','none',0,0,'{}')"
    )
    conn.commit()

    assert set(repo.get_unseen_paths(80)) == {"p2"}
    listed = repo.list_files(limit=10, repo="repo2", root_ids=["root2"])
    assert len(listed) == 1
    assert listed[0]["path"] == "p2"
    stats = repo.get_repo_stats(root_ids=["root1", "root2"])
    assert stats["repo1"] >= 1
    assert stats["repo2"] >= 1


def test_file_repository_delete_and_update_last_seen():
    repo, conn = _make_repo()
    cur = conn.cursor()
    repo.upsert_files_tx(cur, [("p3", "rel3", "root3", "repo3", 100, 1, b"")])
    conn.execute("INSERT INTO symbols(path) VALUES('p3')")
    conn.execute("INSERT INTO symbol_relations(from_path,to_path) VALUES('p3','x')")
    conn.commit()

    repo.update_last_seen_tx(cur, [], 123)  # no-op path
    repo.update_last_seen_tx(cur, ["p3"], 123)
    conn.commit()
    seen = conn.execute("SELECT last_seen_ts FROM files WHERE path='p3'").fetchone()
    assert seen["last_seen_ts"] == 123

    repo.delete_path_tx(cur, "p3")
    conn.commit()
    assert conn.execute("SELECT 1 FROM files WHERE path='p3'").fetchone() is None
    assert conn.execute("SELECT 1 FROM symbols WHERE path='p3'").fetchone() is None
    assert conn.execute("SELECT 1 FROM symbol_relations WHERE from_path='p3'").fetchone() is None
