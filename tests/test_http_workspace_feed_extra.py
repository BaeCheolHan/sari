from sari.core.http_workspace_feed import _parse_failed_row, build_registered_workspaces_payload


def test_parse_failed_row_handles_non_numeric_values():
    rid, pending, failed = _parse_failed_row(("rid-1", "x", "y"))
    assert rid == "rid-1"
    assert pending == 0
    assert failed == 0


def test_build_registered_workspaces_payload_parses_failed_rows_safely():
    warns = []

    def _warn_status(code: str, message: str, **kwargs):
        warns.append((code, message, kwargs))

    class _Cursor:
        def fetchall(self):
            return [("rid-1", "x", "y")]

    class _DB:
        @staticmethod
        def get_roots():
            return [{"path": "/tmp/ws", "root_id": "rid-1", "file_count": 1}]

        @staticmethod
        def execute(_sql: str):
            return _Cursor()

    out = build_registered_workspaces_payload(
        workspace_root="/tmp/ws",
        db=_DB(),
        indexer=object(),
        normalize_workspace_path_with_meta=lambda p: (p, "workspace_manager"),
        indexer_workspace_roots=lambda _idx: ["/tmp/ws"],
        status_warning_counts_provider=lambda: {},
        warn_status=_warn_status,
    )
    assert out["workspaces"]
    row = out["workspaces"][0]
    assert row["pending_count"] == 0
    assert row["failed_count"] == 0
    assert warns == []
