import types

from sari.mcp.tools.read_file import _count_tokens


def test_count_tokens_fallback_uses_utf8_bytes_for_cjk(monkeypatch):
    fake_tiktoken = types.SimpleNamespace()
    monkeypatch.setitem(__import__("sys").modules, "tiktoken", fake_tiktoken)

    content = "한글토큰추정abc"
    estimated = _count_tokens(content)

    assert estimated >= len(content.encode("utf-8")) // 4
