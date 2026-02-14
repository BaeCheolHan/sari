import json

import sari.mcp.tools.read as read_tool


def _payload(resp: dict) -> dict:
    return json.loads(resp["content"][0]["text"])


def _hash12(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


class _Conn:
    def execute(self, sql: str, params: tuple):
        class _Cur:
            def __init__(self, q: str, p: tuple):
                self._q = q
                self._p = p

            def fetchall(self):
                if "FROM symbols" in self._q and self._p and self._p[0] == "rid/Service.java":
                    return [
                        {
                            "name": "target",
                            "qualname": "Service.target",
                            "kind": "method",
                            "line": 2,
                            "end_line": 4,
                        }
                    ]
                if "FROM symbol_relations" in self._q:
                    return []
                return []

        return _Cur(sql, params)


class _DB:
    def __init__(self) -> None:
        self._read = _Conn()

    def get_read_connection(self):
        return self._read


def test_ast_edit_java_symbol_uses_db_span_without_ast(monkeypatch, tmp_path):
    monkeypatch.setenv("SARI_FORMAT", "json")
    ws = tmp_path / "ws"
    ws.mkdir()
    f = ws / "Service.java"
    before = "class Service {\n  int target() {\n    return 1;\n  }\n}\n"
    f.write_text(before, encoding="utf-8")
    db = _DB()

    monkeypatch.setattr(read_tool, "resolve_db_path", lambda *_a, **_k: "rid/Service.java")
    monkeypatch.setattr(read_tool, "resolve_fs_path", lambda *_a, **_k: str(f))

    resp = read_tool._execute_ast_edit(
        {
            "target": str(f),
            "expected_version_hash": _hash12(before),
            "symbol": "target",
            "new_text": "int target() {\n    return 2;\n  }",
        },
        db,
        [str(ws)],
    )
    payload = _payload(resp)
    assert payload.get("isError") is not True
    assert payload["updated"] is True
    assert "return 2;" in f.read_text(encoding="utf-8")

