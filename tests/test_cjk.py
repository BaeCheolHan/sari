import pytest
from sari.core.cjk import has_cjk, _is_cjk_char, cjk_space

def test_has_cjk():
    assert has_cjk("hello") is False
    assert has_cjk("안녕") is True
    assert has_cjk("こんにちは") is True
    assert has_cjk("你好") is True

def test_is_cjk_char():
    assert _is_cjk_char('A') is False
    assert _is_cjk_char('한') is True
    assert _is_cjk_char('あ') is True

def test_cjk_space():
    # Basic check for CJK spacing logic
    text = "한글hello"
    spaced = cjk_space(text)
    assert len(spaced) >= len(text)