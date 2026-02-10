import pytest
from sari.core.parsers.ast_engine import ASTEngine


def test_kotlin_support_verification():
    engine = ASTEngine()
    lang_obj = engine._get_language("kotlin")
    if lang_obj is None:
        pytest.skip("kotlin parser is not available in this runtime")

    code = "@RestController class MyClass { fun myFun() {} }"
    symbols, relations = engine.extract_symbols("main.kt", "kotlin", code)
    assert isinstance(symbols, list)
    assert isinstance(relations, list)
