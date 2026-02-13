from __future__ import annotations

from sari.mcp.server_bootstrap import build_runtime_options, parse_truthy_flag


def test_parse_truthy_flag_accepts_common_true_values():
    assert parse_truthy_flag("1") is True
    assert parse_truthy_flag("true") is True
    assert parse_truthy_flag("YES") is True
    assert parse_truthy_flag(" on ") is True
    assert parse_truthy_flag("0") is False
    assert parse_truthy_flag("") is False


def test_build_runtime_options_keeps_debug_semantics_and_defaults():
    opts = build_runtime_options(
        env={"SARI_MCP_DEBUG": "1", "SARI_DEV_JSONL": "yes"},
        debug_default=False,
        queue_size=123,
    )
    assert opts.debug_enabled is True
    assert opts.dev_jsonl is True
    assert opts.force_content_length is False
    assert opts.queue_size == 123
    assert opts.max_workers == 4

    opts2 = build_runtime_options(
        env={"SARI_MCP_DEBUG": "true", "SARI_FORCE_CONTENT_LENGTH": "on", "SARI_MCP_WORKERS": ""},
        debug_default=False,
        queue_size=50,
    )
    # Existing behavior: debug env only enables when exactly "1".
    assert opts2.debug_enabled is False
    assert opts2.force_content_length is True
    assert opts2.max_workers == 4
