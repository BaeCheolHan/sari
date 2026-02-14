from unittest.mock import MagicMock

from sari.mcp.tools.list_symbols import execute_list_symbols
from sari.core.models import IndexingResult
from sari.core.workspace import WorkspaceManager


class _SeqConn:
    def __init__(self):
        self.calls = 0

    def execute(self, sql, _params):
        self.calls += 1

        class _Cur:
            def __init__(self, sql_text, n):
                self._sql = sql_text
                self._n = n

            def fetchall(self):
                if "FROM symbols" not in self._sql:
                    return []
                if self._n == 1:
                    return []
                return [
                    {
                        "name": "AuthService",
                        "kind": "class",
                        "line": 1,
                        "end_line": 30,
                        "parent": "",
                        "qualname": "AuthService",
                    }
                ]

            def fetchone(self):
                return None

        return _Cur(sql, self.calls)


def test_list_symbols_hydrates_when_db_symbols_empty(monkeypatch):
    db = MagicMock()
    db.get_read_connection.return_value = _SeqConn()

    monkeypatch.setattr("sari.mcp.tools.list_symbols.resolve_db_path", lambda *_a, **_k: "rid/src/auth.py")
    monkeypatch.setattr("sari.mcp.tools.list_symbols.hydrate_file_symbols", lambda **_k: ("rid/src/auth.py", 1))

    resp = execute_list_symbols({"path": "src/auth.py", "repo": "repo1"}, db, ["/tmp/ws"])
    text = resp["content"][0]["text"]

    assert "PACK1 tool=list_symbols ok=true" in text
    assert "AuthService" in text


def test_ondemand_hydrate_updates_lsp_cache_tables(db, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    rid = WorkspaceManager.root_id_for_workspace(str(ws))
    db.upsert_root(rid, str(ws), str(ws))

    rel = "src/auth.py"
    file_path = ws / rel
    content = "class AuthService:\n    def login(self):\n        return True\n"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

    row = IndexingResult(
        path=f"{rid}/{rel}",
        rel=rel,
        root_id=rid,
        repo="repo1",
        type="changed",
        content=content,
        fts_content=content,
        mtime=1,
        size=len(content),
        content_hash="h-auth",
        scan_ts=1,
        metadata_json="{}",
    )
    db.upsert_files_turbo([row])
    db.finalize_turbo_batch()

    out = execute_list_symbols({"path": str(file_path), "repo": "repo1"}, db, [str(ws)])
    assert out.get("isError") is not True

    conn = db.get_connection()
    db_path = f"{rid}/{rel}"
    cache = conn.execute(
        "SELECT dirty, row_version FROM lsp_indexed_files WHERE path = ?",
        (db_path,),
    ).fetchone()
    assert cache is not None
    assert int(cache[0]) == 0
    assert int(cache[1]) > 0

    sym = conn.execute("SELECT COUNT(1) FROM lsp_symbols WHERE path = ?", (db_path,)).fetchone()
    assert sym is not None
    assert int(sym[0]) >= 1
