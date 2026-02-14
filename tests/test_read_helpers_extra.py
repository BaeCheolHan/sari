from sari.mcp.tools.read import _attach_context_refs, _wait_focus_sync


def test_attach_context_refs_tolerates_non_integer_span_fields():
    refs = [{"kind": "file", "path": "rid/a.py", "start_line": "x", "end_line": "y", "content_hash": "h"}]
    out = _attach_context_refs(refs, ["/tmp/ws"])
    assert out
    assert out[0]["path"] == "rid/a.py"


def test_wait_focus_sync_tolerates_non_integer_queue_depths():
    class _Indexer:
        @staticmethod
        def get_queue_depths():
            return {"fair_queue": "x", "priority_queue": "y", "db_writer": "z"}

    state, warning = _wait_focus_sync(_Indexer(), 1)
    assert state in {"complete", "timeout"}
    assert isinstance(warning, str)
