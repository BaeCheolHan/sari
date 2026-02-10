from __future__ import annotations

import sqlite3

from sari.core.models import CallerHitDTO, ImplementationHitDTO
from sari.core.services.symbol_service import SymbolService


def test_snippet_repository_normalization_paths(db):
    rid = "root-snip"
    db.upsert_root(rid, "/tmp/ws", "/tmp/ws")
    repo = db.snippets
    cur = db._write.cursor()

    # Tool format
    tool_row = (
        "tag1",
        f"{rid}/a.py",
        10,
        20,
        "content-a",
        "h1",
        "ab",
        "aa",
        "repo1",
        rid,
        "note-a",
        "c1",
        100,
        101,
        "{}",
    )
    # Schema format
    schema_row = (
        "tag1",
        f"{rid}/b.py",
        rid,
        1,
        2,
        "content-b",
        "h2",
        "",
        "",
        "repo1",
        "note-b",
        "c2",
        200,
        201,
        "{}",
    )

    inserted = repo.upsert_snippet_tx(cur, [tool_row, schema_row])
    db._write.commit()
    assert inserted == 2

    rows = repo.list_snippets_by_tag("tag1", limit=10)
    paths = {r.path for r in rows}
    assert f"{rid}/a.py" in paths
    assert f"{rid}/b.py" in paths


def test_context_repository_normalization_and_upsert(db):
    repo = db.contexts
    cur = db._write.cursor()

    # Partial row gets padded and coerced
    inserted = repo.upsert_context_tx(cur, [("topic-x", "content-x", "[]", "[]", "manual")])
    db._write.commit()
    assert inserted == 1

    got = repo.get_context_by_topic("topic-x")
    assert got is not None
    assert got.topic == "topic-x"

    # Upsert API with dict
    obj = repo.upsert({"topic": "topic-y", "content": "hello", "tags": ["a"], "related_files": ["f.py"]})
    assert obj.topic == "topic-y"
    found = repo.search_contexts("hello", limit=5)
    assert any(x.topic == "topic-y" for x in found)


def test_extra_repositories_accept_dict_rows(db):
    rid = "root-dict"
    db.upsert_root(rid, "/tmp/ws-dict", "/tmp/ws-dict")
    cur = db._write.cursor()

    inserted_snip = db.snippets.upsert_snippet_tx(
        cur,
        [
            {
                "tag": "dict-tag",
                "path": f"{rid}/dict.py",
                "start": 3,
                "end": 9,
                "content": "dict-content",
                "content_hash": "h-dict",
                "anchor_before": "",
                "anchor_after": "",
                "repo": "repo-dict",
                "root_id": rid,
                "note": "n",
                "commit_hash": "c",
                "created_ts": 1,
                "updated_ts": 2,
                "metadata_json": "{}",
            }
        ],
    )
    inserted_ctx = db.contexts.upsert_context_tx(
        cur,
        [
            {
                "topic": "dict-topic",
                "content": "dict-content",
                "tags_json": "[]",
                "related_files_json": "[]",
                "source": "manual",
                "valid_from": 0,
                "valid_until": 0,
                "deprecated": 0,
                "created_ts": 1,
                "updated_ts": 2,
            }
        ],
    )
    db._write.commit()

    assert inserted_snip == 1
    assert inserted_ctx == 1
    assert db.snippets.list_snippets_by_tag("dict-tag", limit=5)[0].path == f"{rid}/dict.py"
    assert db.contexts.get_context_by_topic("dict-topic") is not None


def test_failed_task_repository_accepts_dict_rows(db):
    db.upsert_root("root", "/tmp/ws-root", "/tmp/ws-root")
    cur = db._write.cursor()
    inserted = db.tasks.upsert_failed_tasks_tx(
        cur,
        [
            {
                "path": "/tmp/fail.py",
                "root_id": "root",
                "attempts": 2,
                "error": "boom",
                "ts": 10,
                "next_retry": 10,
                "metadata_json": "{}",
            }
        ],
    )
    db._write.commit()
    assert inserted == 1
    ready = db.tasks.list_failed_tasks_ready(10, limit=10)
    assert any(item["path"] == "/tmp/fail.py" for item in ready)


def test_symbol_service_direct_and_fallback_paths(monkeypatch):
    class _Conn:
        def __init__(self, rows=None, symbol_row=None, fail=False):
            self.rows = rows or []
            self.symbol_row = symbol_row
            self.fail = fail

        def execute(self, sql, params):
            if self.fail and "symbol_relations" in sql:
                raise sqlite3.OperationalError("db down")
            self._last_sql = sql
            self._last_params = params
            return self

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.symbol_row

    class _DB:
        def __init__(self, conn):
            self._conn = conn

        def get_read_connection(self):
            return self._conn

    direct_conn = _Conn(rows=[("/p.py", "Impl", "sid-impl", "implements", 12)])
    svc = SymbolService(_DB(direct_conn))
    out = svc.get_implementations("Base", symbol_id="sid-base", limit="2")
    assert len(out) == 1
    assert out[0]["implementer_sid"] == "sid-impl"

    fail_conn = _Conn(rows=[], symbol_row=("sid-a", "AImpl"), fail=True)
    svc2 = SymbolService(_DB(fail_conn))
    # Force fallback to return stable output so we only test direct-search exception boundary
    monkeypatch.setattr(svc2, "_fallback_text_search", lambda *args, **kwargs: [{"implementer_path": "/f.py", "implementer_symbol": "AImpl", "implementer_sid": "sid-a", "rel_type": "extends", "line": 3}])
    out2 = svc2.get_implementations("Base", symbol_id="", limit="bad")
    assert len(out2) == 1
    assert out2[0]["implementer_symbol"] == "AImpl"


def test_symbol_service_limit_normalization(db):
    svc = SymbolService(db)
    assert svc._normalize_limit("bad") == 100
    assert svc._normalize_limit(-1) == 1
    assert svc._normalize_limit(10000) == 500


def test_caller_and_implementation_dto_from_tuple_and_dict():
    c1 = CallerHitDTO.from_row(("p.py", "caller", "sid-c", 7, "calls"))
    assert c1.caller_path == "p.py"
    assert c1.line == 7

    c2 = CallerHitDTO.from_row({"from_path": "x.py", "from_symbol": "x", "from_symbol_id": "sid-x", "line": 1, "rel_type": "calls"})
    assert c2.caller_symbol == "x"

    i1 = ImplementationHitDTO.from_row(("i.py", "Impl", "sid-i", "extends", 3))
    assert i1.implementer_sid == "sid-i"

    i2 = ImplementationHitDTO.from_row({"from_path": "j.py", "from_symbol": "J", "from_symbol_id": "sid-j", "line": 9, "rel_type": "implements"})
    assert i2.implementer_path == "j.py"
