import os

from sari.mcp.tools.read import execute_read


def test_read_symbol_pack_emits_single_sari_next_line(monkeypatch):
    os.environ["SARI_FORMAT"] = "pack"
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")

    def _fake_read_symbol(_args, _db, _logger, _roots):
        return {
            "content": [
                {
                    "type": "text",
                    "text": "PACK1 tool=read_symbol ok=true returned=1\nr:path=repo/s.py",
                }
            ]
        }

    monkeypatch.setattr("sari.mcp.tools.read.execute_read_symbol", _fake_read_symbol)
    result = execute_read({"mode": "symbol", "target": "MySym", "path": "repo/s.py"}, object(), ["/tmp/ws"])
    text = result["content"][0]["text"]
    assert "PACK1 tool=read_symbol ok=true" in text
    assert "\nSARI_NEXT: get_callers(" in text
    assert text.count("\nSARI_NEXT: ") == 1


def test_read_file_pack_does_not_emit_sari_next_line(monkeypatch):
    os.environ["SARI_FORMAT"] = "pack"
    monkeypatch.setenv("SARI_READ_GATE_MODE", "warn")

    def _fake_read_file(_args, _db, _roots):
        return {
            "content": [
                {
                    "type": "text",
                    "text": "PACK1 tool=read_file ok=true returned=1\nr:path=repo/a.py",
                }
            ]
        }

    monkeypatch.setattr("sari.mcp.tools.read.execute_read_file", _fake_read_file)
    result = execute_read({"mode": "file", "target": "repo/a.py", "offset": 0, "limit": 1}, object(), ["/tmp/ws"])
    text = result["content"][0]["text"]
    assert "PACK1 tool=read_file ok=true" in text
    assert "\nSARI_NEXT: " not in text
