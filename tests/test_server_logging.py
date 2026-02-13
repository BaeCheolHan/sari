from __future__ import annotations

from sari.mcp.server_logging import (
    log_debug_message,
    log_debug_request,
    log_debug_response,
)


class _Logger:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def debug(self, event: str, **kwargs):
        self.calls.append((event, kwargs))


def test_log_debug_message_respects_debug_flag():
    logger = _Logger()
    log_debug_message(False, logger, "hello")
    assert logger.calls == []
    log_debug_message(True, logger, "hello")
    assert logger.calls == [("mcp_debug_log", {"message": "hello"})]


def test_log_debug_request_sanitizes_tool_arguments():
    logger = _Logger()

    def sanitize(value, key=""):
        return f"safe:{key}:{value}"

    req = {
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search", "arguments": {"api_key": "secret", "query": "x"}},
    }
    log_debug_request(True, logger, "stdin", req, sanitize)
    assert len(logger.calls) == 1
    event, payload = logger.calls[0]
    assert event == "mcp_request"
    assert payload["tool"] == "search"
    assert payload["argument_keys"] == ["api_key", "query"]
    assert payload["arguments"]["api_key"] == "safe:api_key:secret"


def test_log_debug_response_sanitizes_error_payload():
    logger = _Logger()

    def sanitize(value, key=""):
        return {"safe": True, "key": key, "value": value}

    resp = {"id": 1, "error": {"message": "boom"}}
    log_debug_response(True, logger, "stdout", resp, sanitize)
    assert len(logger.calls) == 1
    event, payload = logger.calls[0]
    assert event == "mcp_response"
    assert payload["has_error"] is True
    assert payload["error"]["safe"] is True
