import pytest
from unittest.mock import MagicMock

from sari.core.services.call_graph.service import CallGraphService


def test_call_graph_service_rejects_non_object_args():
    svc = CallGraphService(MagicMock(), ["/tmp/ws"])
    with pytest.raises(ValueError, match="args must be an object"):
        svc.build(["bad-args"])


def test_call_graph_fuzzy_fallback_is_disabled_when_symbol_id_is_given():
    class _Symbols:
        def fuzzy_search_symbols(self, _query, limit=3):
            return [{"path": "rid/a.py", "name": "Target", "kind": "function", "line": 1, "end_line": 2, "qualname": "Target", "symbol_id": "sid-t"}]

    class _Conn:
        def execute(self, _sql, _params):
            class _Cur:
                def fetchall(self_inner):
                    return []

            return _Cur()

    class _DB:
        symbols = _Symbols()

        def get_read_connection(self):
            return _Conn()

        def get_symbol_fan_in_stats(self, _names):
            return {}

    svc = CallGraphService(_DB(), [])
    payload = svc.build({"symbol_id": "sid-missing", "name": "Targte", "depth": 1})
    assert payload["symbol_id"] == "sid-missing"
    assert payload["summary"]["upstream_count"] == 0
    assert payload["summary"]["downstream_count"] == 0
    assert "fuzzy match" not in str(payload.get("scope_reason", "")).lower()


def test_call_graph_fuzzy_fallback_respects_root_scope():
    class _DTO:
        def __init__(self, path, name, symbol_id):
            self.path = path
            self.name = name
            self.kind = "function"
            self.line = 1
            self.end_line = 2
            self.qualname = name
            self.symbol_id = symbol_id

        def model_dump(self):
            return {
                "path": self.path,
                "name": self.name,
                "kind": self.kind,
                "line": self.line,
                "end_line": self.end_line,
                "qualname": self.qualname,
                "symbol_id": self.symbol_id,
            }

    class _Symbols:
        def fuzzy_search_symbols(self, _query, limit=3):
            return [
                _DTO("rid-out/target.py", "Target", "sid-out"),
                _DTO("rid-in/target.py", "Target", "sid-in"),
            ]

    class _Conn:
        def execute(self, _sql, _params):
            class _Cur:
                def fetchall(self_inner):
                    return []

            return _Cur()

    class _DB:
        symbols = _Symbols()

        def get_read_connection(self):
            return _Conn()

        def get_symbol_fan_in_stats(self, _names):
            return {}

    svc = CallGraphService(_DB(), [])
    payload = svc.build({"name": "Targte", "depth": 1, "root_ids": ["rid-in"]})
    assert payload["path"] == "rid-in/target.py"
    assert payload["symbol_id"] == "sid-in"
    assert "fuzzy match" in str(payload.get("scope_reason", "")).lower()


def test_call_graph_hides_builtin_calls_by_default_and_can_include_them():
    class _Cur:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _Conn:
        def execute(self, sql, _params):
            if "FROM symbols" in sql:
                return _Cur(
                    [
                        {
                            "path": "rid/main.py",
                            "name": "main",
                            "kind": "function",
                            "line": 1,
                            "end_line": 5,
                            "qualname": "main",
                            "symbol_id": "sid-main",
                        }
                    ]
                )
            if "WHERE from_symbol_id = ?" in sql:
                return _Cur(
                    [
                        {"to_path": "rid/main.py", "to_symbol": "print", "to_symbol_id": "sid-print", "line": 2, "rel_type": "calls"},
                        {"to_path": "rid/main.py", "to_symbol": "helper", "to_symbol_id": "sid-helper", "line": 3, "rel_type": "calls"},
                    ]
                )
            return _Cur([])

    class _DB:
        def get_read_connection(self):
            return _Conn()

        def get_symbol_fan_in_stats(self, names):
            return {str(n): 0 for n in names}

    svc = CallGraphService(_DB(), [])
    hidden = svc.build({"symbol": "main", "path": "rid/main.py", "depth": 1})
    hidden_names = [c["name"] for c in hidden["downstream"]["children"]]
    assert "print" not in hidden_names
    assert "helper" in hidden_names

    shown = svc.build({"symbol": "main", "path": "rid/main.py", "depth": 1, "include_builtins": True})
    shown_names = [c["name"] for c in shown["downstream"]["children"]]
    assert "print" in shown_names
