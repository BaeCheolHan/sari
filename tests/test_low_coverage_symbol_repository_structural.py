from __future__ import annotations

from sari.core.repository.symbol_repository import SymbolRepository


def _insert_file_row(cur, row):
    cur.execute(
        """
        INSERT INTO files (
            path, rel_path, root_id, repo, mtime, size, content, hash, fts_content,
            last_seen_ts, deleted_ts, status, error, parse_status, parse_error,
            ast_status, ast_reason, is_binary, is_minified, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        row,
    )


def test_symbol_repository_upsert_with_legacy_and_new_rows(db):
    repo: SymbolRepository = db.symbols
    cur = db._write.cursor()
    db.upsert_root("rid", "/tmp/ws", "/tmp/ws")
    _insert_file_row(
        cur,
        ("rid/main.py", "main.py", "rid", "repo", 1, 10, "", "h1", "", 0, 0, "ok", "", "ok", "", "none", "none", 0, 0, "{}"),
    )
    _insert_file_row(
        cur,
        ("rid/util.py", "util.py", "rid", "repo", 1, 10, "", "h2", "", 0, 0, "ok", "", "ok", "", "none", "none", 0, 0, "{}"),
    )

    # legacy tuple (short format, < 12 to follow old-format branch)
    legacy = (
        "rid/main.py",
        "main",
        "function",
        10,
        12,
        "def main(): pass",
        "",
        "{}",
        "",
        "main",
        "sid-main",
    )
    # new format row aligned with SYMBOL_COLUMNS
    new = (
        "sid-util",
        "rid/util.py",
        "rid",
        "util",
        "function",
        1,
        2,
        "def util(): pass",
        "",
        "{}",
        "",
        "util",
        1.0,
    )

    n = repo.upsert_symbols_tx(cur, [legacy, new])
    db._write.commit()
    assert n == 2

    symbols_main = repo.list_symbols_by_path("rid/main.py")
    assert len(symbols_main) == 1
    assert symbols_main[0].name == "main"

    symbols_util = repo.list_symbols_by_path("rid/util.py")
    assert len(symbols_util) == 1
    assert symbols_util[0].symbol_id == "sid-util"


def test_symbol_repository_relations_and_fan_in(db):
    repo: SymbolRepository = db.symbols
    cur = db._write.cursor()
    db.upsert_root("rid", "/tmp/ws", "/tmp/ws")
    _insert_file_row(
        cur,
        ("rid/a.py", "a.py", "rid", "repo", 1, 10, "", "h1", "", 0, 0, "ok", "", "ok", "", "none", "none", 0, 0, "{}"),
    )
    _insert_file_row(
        cur,
        ("rid/b.py", "b.py", "rid", "repo", 1, 10, "", "h2", "", 0, 0, "ok", "", "ok", "", "none", "none", 0, 0, "{}"),
    )

    repo.upsert_symbols_tx(cur, [
        ("sid-a", "rid/a.py", "rid", "A", "class", 1, 10, "class A", "", "{}", "", "A", 0.0),
        ("sid-b", "rid/b.py", "rid", "B", "class", 1, 10, "class B", "", "{}", "", "B", 0.0),
    ])
    repo.upsert_relations_tx(cur, [
        ("rid/b.py", "rid", "B", "sid-b", "rid/a.py", "rid", "A", "sid-a", "extends", 2, "{}")
    ])
    db._write.commit()

    stats = repo.get_symbol_fan_in_stats(["A", "B"])
    assert stats.get("A", 0) >= 1


def test_symbol_repository_fuzzy_and_importance(db):
    repo: SymbolRepository = db.symbols
    cur = db._write.cursor()
    db.upsert_root("rid", "/tmp/ws", "/tmp/ws")
    _insert_file_row(
        cur,
        ("rid/h.py", "h.py", "rid", "repo", 1, 10, "", "h1", "", 0, 0, "ok", "", "ok", "", "none", "none", 0, 0, "{}"),
    )
    _insert_file_row(
        cur,
        ("rid/u.py", "u.py", "rid", "repo", 1, 10, "", "h2", "", 0, 0, "ok", "", "ok", "", "none", "none", 0, 0, "{}"),
    )
    repo.upsert_symbols_tx(cur, [
        ("sid-hello", "rid/h.py", "rid", "HelloService", "class", 1, 20, "class HelloService", "", "{}", "", "HelloService", 0.0),
        ("sid-user", "rid/u.py", "rid", "UserService", "class", 1, 20, "class UserService", "", "{}", "", "UserService", 0.0),
    ])
    repo.upsert_relations_tx(cur, [
        ("rid/u.py", "rid", "UserService", "sid-user", "rid/h.py", "rid", "HelloService", "sid-hello", "calls", 3, "{}")
    ])
    db._write.commit()

    fuzzy = repo.fuzzy_search_symbols("HelloServcie", limit=3)
    assert any(s.name == "HelloService" for s in fuzzy)

    updated = repo.recalculate_symbol_importance()
    assert updated >= 1


def test_symbol_repository_fuzzy_prefilters_candidates(monkeypatch, db):
    repo: SymbolRepository = db.symbols
    calls: list[tuple[str, object]] = []

    class _Cur:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    def _fake_execute(sql, params=None):
        calls.append((sql, params))
        if "SELECT DISTINCT name FROM symbols WHERE LOWER(name) LIKE ?" in sql:
            return _Cur([{"name": "HelloService"}, {"name": "UserService"}])
        if "WHERE s.name IN" in sql:
            return _Cur([
                {
                    "symbol_id": "sid-hello",
                    "path": "rid/h.py",
                    "root_id": "rid",
                    "repo": "repo",
                    "name": "HelloService",
                    "kind": "class",
                    "line": 1,
                    "end_line": 20,
                    "content": "class HelloService",
                    "parent": "",
                    "meta_json": "{}",
                    "doc_comment": "",
                    "qualname": "HelloService",
                    "importance_score": 1.0,
                }
            ])
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(repo, "execute", _fake_execute)

    hits = repo.fuzzy_search_symbols("HelloServcie", limit=3)
    assert len(hits) == 1
    assert hits[0].name == "HelloService"
    assert any("SELECT DISTINCT name FROM symbols WHERE LOWER(name) LIKE ?" in sql for sql, _ in calls)
