from __future__ import annotations

import types

from sari.services.collection.l3_tree_sitter_outline import TreeSitterOutlineExtractor
from sari.services.collection.l3_tree_sitter_outline import TreeSitterOutlineResult


def test_load_language_uses_fallback_module_loader(monkeypatch) -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._get_language = None
    extractor._language_cls = lambda capsule: ("wrapped", capsule)  # type: ignore[assignment]

    fake_module = types.SimpleNamespace(language=lambda: "capsule:python")
    monkeypatch.setattr(
        "sari.services.collection.l3_tree_sitter_outline.importlib.import_module",
        lambda name: fake_module if name == "tree_sitter_python" else None,
    )

    loaded = extractor._load_language("python")

    assert loaded == ("wrapped", "capsule:python")


def test_load_language_returns_none_for_unknown_language() -> None:
    extractor = TreeSitterOutlineExtractor()

    assert extractor._load_language("unknown") is None


def test_build_parser_supports_legacy_set_language_path() -> None:
    extractor = TreeSitterOutlineExtractor()

    class _LegacyParser:
        def __init__(self) -> None:
            self.language = None

        def set_language(self, language) -> None:  # noqa: ANN001
            self.language = language

    class _CtorRaisesTypeError:
        def __call__(self, language=None):  # noqa: ANN001
            if language is not None:
                raise TypeError("legacy")
            return _LegacyParser()

    extractor._parser_cls = _CtorRaisesTypeError()  # type: ignore[assignment]
    parser = extractor._build_parser("lang:python")

    assert parser is not None
    assert parser.language == "lang:python"


def test_extract_outline_prefers_query_strategy(monkeypatch) -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._available = True
    extractor._get_or_create_parser = lambda normalized_lang: object()  # type: ignore[assignment]
    extractor._extract_outline_with_query = (  # type: ignore[method-assign]
        lambda **kwargs: TreeSitterOutlineResult(symbols=[{"name": "A", "kind": "class", "line": 1, "end_line": 1}], degraded=False)
    )

    called = {"legacy": False}

    def _legacy(**kwargs):  # noqa: ANN003
        called["legacy"] = True
        return TreeSitterOutlineResult(symbols=[], degraded=False)

    extractor._extract_outline_legacy = _legacy  # type: ignore[method-assign]

    result = extractor.extract_outline(lang_key="java", content_text="class A {}", budget_sec=0.1)

    assert result.degraded is False
    assert result.symbols
    assert called["legacy"] is False


def test_extract_outline_falls_back_to_legacy_when_query_unavailable(monkeypatch) -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._available = True
    extractor._get_or_create_parser = lambda normalized_lang: object()  # type: ignore[assignment]
    extractor._extract_outline_with_query = lambda **kwargs: None  # type: ignore[method-assign]

    extractor._extract_outline_legacy = (  # type: ignore[method-assign]
        lambda **kwargs: TreeSitterOutlineResult(symbols=[{"name": "B", "kind": "class", "line": 2, "end_line": 2}], degraded=False)
    )

    result = extractor.extract_outline(lang_key="java", content_text="class B {}", budget_sec=0.1)

    assert result.degraded is False
    assert result.symbols[0]["name"] == "B"


def test_extract_outline_supports_query_captures_dict_shape() -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._available = True

    class _Node:
        def __init__(self, node_type: str, line: int, text: bytes, parent=None) -> None:
            self.type = node_type
            self.start_point = (line - 1, 0)
            self.end_point = (line - 1, 1)
            self.text = text
            self.parent = parent

    symbol_node = _Node("class_declaration", 3, b"class Foo {}")
    name_node = _Node("identifier", 3, b"Foo", parent=symbol_node)
    symbol_node.parent = None

    class _Parser:
        def parse(self, data):  # noqa: ANN001
            return types.SimpleNamespace(root_node=object())

    extractor._get_or_create_parser = lambda normalized_lang: _Parser()  # type: ignore[assignment]
    extractor._languages["java"] = object()
    extractor._compiled_queries["java"] = object()
    extractor._query_cls = object  # type: ignore[assignment]
    extractor._run_query_captures = lambda **kwargs: {  # type: ignore[method-assign]
        "symbol.class": [symbol_node],
        "name": [name_node],
    }

    result = extractor.extract_outline(lang_key="java", content_text="class Foo {}", budget_sec=0.1)

    assert result.degraded is False
    assert result.symbols
    assert result.symbols[0]["name"] == "Foo"
    assert result.symbols[0]["kind"] == "class"
