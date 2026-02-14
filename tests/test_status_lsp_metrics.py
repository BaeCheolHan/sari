import json

from sari.mcp.tools.status import execute_status


class _DB:
    class _DBSQL:
        @staticmethod
        def execute_sql(_q: str):
            class _Cur:
                @staticmethod
                def fetchone():
                    return {"count_files": 0, "count_symbols": 0}

            return _Cur()

    db = _DBSQL()


def test_status_includes_lsp_metrics(monkeypatch):
    class _Hub:
        @staticmethod
        def metrics_snapshot():
            return {
                "language_cold_start_count": 3,
                "lsp_restart_count": 1,
                "lsp_timeout_rate": 0.25,
                "lsp_backpressure_count": 2,
            }

    monkeypatch.setenv("SARI_FORMAT", "json")
    monkeypatch.setattr("sari.core.lsp.hub.get_lsp_hub", lambda: _Hub())

    out = execute_status({}, indexer=None, db=_DB(), cfg=None, workspace_root="/tmp/ws", server_version="x")
    payload = json.loads(out["content"][0]["text"])
    assert payload["language_cold_start_count"] == 3
    assert payload["lsp_restart_count"] == 1
    assert payload["lsp_timeout_rate"] == 0.25
    assert payload["lsp_backpressure_count"] == 2

