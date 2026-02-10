from sari.core.models import ParseResult
from sari.core.parsers.common import _safe_compile
from sari.core.parsers.generic import GenericRegexParser


def test_generic_parser_returns_parse_result_object():
    parser = GenericRegexParser(
        {
            "re_class": _safe_compile(r"\b(class)\s+([A-Za-z_][A-Za-z0-9_]*)"),
            "re_method": _safe_compile(r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
        },
        ".py",
    )
    result = parser.extract("x.py", "class A:\n    def m(self):\n        pass\n")

    assert isinstance(result, ParseResult)
    assert len(result.symbols) >= 1
    assert isinstance(result.relations, list)

