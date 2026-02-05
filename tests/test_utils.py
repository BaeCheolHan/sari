import pytest
from pathlib import Path
from sari.core.utils.security import _redact
from sari.core.utils.file import _parse_size, _is_minified
from sari.core.utils.text import _normalize_engine_text

def test_redaction():
    text = "openai_api_key = 'sk-1234567890abcdef'"
    redacted = _redact(text)
    assert "sk-1234567890" not in redacted
    assert "***" in redacted

def test_parse_size():
    assert _parse_size("1KB", default=0) == 1024
    assert _parse_size("1MB", default=0) == 1024 * 1024
    assert _parse_size(100, default=0) == 100

def test_is_minified():
    normal_code = "def hello():\n    print('world')"
    minified_code = "def hello():print('world');x=1;y=2;z=3;" * 100
    assert _is_minified(Path("test.py"), normal_code) is False
    assert _is_minified(Path("test.js"), minified_code) is True

def test_normalize_text():
    raw = "Hello   \n  World \t !"
    norm = _normalize_engine_text(raw)
    assert "  " not in norm