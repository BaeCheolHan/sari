from __future__ import annotations

import types

from sari.services.collection.l3_tree_sitter_outline import TreeSitterOutlineExtractor


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
