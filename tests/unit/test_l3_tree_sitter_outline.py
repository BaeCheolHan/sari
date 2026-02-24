from __future__ import annotations

import types
from pathlib import Path

from sari.services.collection.l3_asset_loader import L3AssetLoader
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


def test_extract_outline_supports_query_captures_dict_shape_when_name_bucket_comes_first() -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._available = True

    class _Node:
        def __init__(self, node_type: str, line: int, text: bytes, parent=None) -> None:
            self.type = node_type
            self.start_point = (line - 1, 0)
            self.end_point = (line - 1, 1)
            self.text = text
            self.parent = parent

    symbol_node = _Node("class_declaration", 5, b"class Bar {}")
    name_node = _Node("identifier", 5, b"Bar", parent=symbol_node)

    class _Parser:
        def parse(self, data):  # noqa: ANN001
            return types.SimpleNamespace(root_node=object())

    extractor._get_or_create_parser = lambda normalized_lang: _Parser()  # type: ignore[assignment]
    extractor._languages["java"] = object()
    extractor._compiled_queries["java"] = object()
    extractor._query_cls = object  # type: ignore[assignment]
    extractor._run_query_captures = lambda **kwargs: {  # type: ignore[method-assign]
        "name": [name_node],
        "symbol.class": [symbol_node],
    }

    result = extractor.extract_outline(lang_key="java", content_text="class Bar {}", budget_sec=0.1)

    assert result.degraded is False
    assert result.symbols
    assert result.symbols[0]["name"] == "Bar"


def test_java_outline_does_not_emit_field_type_name_as_field_symbol() -> None:
    extractor = TreeSitterOutlineExtractor()
    if not extractor.is_available_for("java"):
        return

    java_src = """
        class Sample {
            private String name;
            private int age;
        }
    """
    result = extractor.extract_outline(lang_key="java", content_text=java_src, budget_sec=0.2)

    assert result.degraded is False
    field_names = {str(s.get("name")) for s in result.symbols if s.get("kind") == "field"}
    assert "name" in field_names
    assert "age" in field_names
    assert "String" not in field_names
    assert "int" not in field_names


def test_java_outline_emits_package_as_module_symbol() -> None:
    extractor = TreeSitterOutlineExtractor()
    if not extractor.is_available_for("java"):
        return

    java_src = """
        package kr.co.vendys.company.api;

        class Sample {
            void run() {}
        }
    """
    result = extractor.extract_outline(lang_key="java", content_text=java_src, budget_sec=0.2)

    assert result.degraded is False
    modules = [s for s in result.symbols if s.get("kind") == "module"]
    assert len(modules) >= 1
    assert any("kr.co.vendys.company.api" in str(s.get("name", "")) for s in modules)


def test_query_source_prefers_packaged_tags_scm(monkeypatch, tmp_path: Path) -> None:
    extractor = TreeSitterOutlineExtractor()
    tags_path = tmp_path / "tree_sitter_java" / "queries" / "tags.scm"
    tags_path.parent.mkdir(parents=True, exist_ok=True)
    tags_path.write_text("(class_declaration name: (identifier) @name) @symbol.class", encoding="utf-8")

    fake_mod = types.SimpleNamespace(__file__=str((tmp_path / "tree_sitter_java" / "__init__.py")))
    monkeypatch.setattr(
        "sari.services.collection.l3_tree_sitter_outline.importlib.import_module",
        lambda name: fake_mod if name == "tree_sitter_java" else (_ for _ in ()).throw(ImportError(name)),
    )

    source = extractor._get_query_source("java")

    assert source is not None
    assert "class_declaration" in source


def test_query_source_falls_back_to_builtin_when_packaged_tags_missing(monkeypatch) -> None:
    extractor = TreeSitterOutlineExtractor()
    monkeypatch.setattr(
        "sari.services.collection.l3_tree_sitter_outline.importlib.import_module",
        lambda name: (_ for _ in ()).throw(ImportError(name)),
    )

    source = extractor._get_query_source("java")

    assert source == extractor._QUERY_SOURCES["java"]


def test_query_source_prefers_asset_query_in_apply_mode(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    (assets / "queries" / "java").mkdir(parents=True, exist_ok=True)
    (assets / "mappings").mkdir(parents=True, exist_ok=True)
    (assets / "manifest.json").write_text('{"version":"test"}', encoding="utf-8")
    (assets / "mappings" / "default.yaml").write_text("{}", encoding="utf-8")
    (assets / "queries" / "java" / "outline.scm").write_text(
        "(class_declaration name: (identifier) @name) @symbol.class",
        encoding="utf-8",
    )
    loader = L3AssetLoader(assets_root=assets)
    extractor = TreeSitterOutlineExtractor(asset_loader=loader, asset_mode="apply")

    source = extractor._get_query_source("java")

    assert source is not None
    assert source.strip() == "(class_declaration name: (identifier) @name) @symbol.class"


def test_run_query_captures_supports_cursor_constructor_with_query() -> None:
    extractor = TreeSitterOutlineExtractor()
    query_obj = object()
    root_obj = object()
    expected = [("node", "symbol.class")]

    class _Cursor:
        def __init__(self, query) -> None:  # noqa: ANN001
            self.query = query

        def captures(self, root):  # noqa: ANN001
            assert self.query is query_obj
            assert root is root_obj
            return expected

    extractor._query_cursor_cls = _Cursor  # type: ignore[assignment]

    got = extractor._run_query_captures(query=query_obj, root=root_obj)

    assert got == expected


def test_language_alias_maps_js_to_javascript() -> None:
    extractor = TreeSitterOutlineExtractor()

    assert extractor._LANGUAGE_ALIASES.get("js") == "javascript"


def test_compile_query_returns_none_when_language_query_raises_name_error() -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._query_cls = None  # type: ignore[assignment]

    class _Lang:
        def query(self, source):  # noqa: ANN001
            raise NameError("Invalid node type function_expression")

    assert extractor._compile_query(language=_Lang(), source="(function_expression) @name") is None


def test_javascript_outline_emits_object_pair_keys_as_field_symbols() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("js"):
        return

    js_src = """
        const obj = {
            foo: 1,
            bar: () => 2,
        };
    """
    result = extractor.extract_outline(lang_key="js", content_text=js_src, budget_sec=0.2)

    assert result.degraded is False
    field_names = {str(s.get("name")) for s in result.symbols if s.get("kind") == "field"}
    assert "foo" in field_names
    assert "bar" in field_names


def test_javascript_outline_emits_string_and_shorthand_object_keys_as_field_symbols() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("js"):
        return

    js_src = """
        const value = 1;
        const obj = {
            "baz": 3,
            value,
        };
    """
    result = extractor.extract_outline(lang_key="js", content_text=js_src, budget_sec=0.2)

    assert result.degraded is False
    field_names = {str(s.get("name")) for s in result.symbols if s.get("kind") == "field"}
    assert "baz" in field_names
    assert "value" in field_names


def test_javascript_outline_emits_callback_function_symbols_from_call_expression() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("js"):
        return

    js_src = """
        promise.catch(() => {});
        app.use((req, res, next) => {});
    """
    result = extractor.extract_outline(lang_key="js", content_text=js_src, budget_sec=0.2)

    assert result.degraded is False
    fn_names = {str(s.get("name")) for s in result.symbols if s.get("kind") == "function"}
    assert "catch" in fn_names
    assert "use" in fn_names


def test_javascript_outline_emits_exported_assignment_function_name() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("js"):
        return

    js_src = """
        module.exports.getAdminUsers = async (adminIds) => {
            return adminIds;
        };
    """
    result = extractor.extract_outline(lang_key="js", content_text=js_src, budget_sec=0.2)

    assert result.degraded is False
    fn_names = {str(s.get("name")) for s in result.symbols if s.get("kind") == "function"}
    assert "getAdminUsers" in fn_names


def test_javascript_outline_emits_computed_object_key_field_symbols() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("js"):
        return

    js_src = """
        const findOption = {
            where: {
                adminId: { [Op.in]: adminIds, [Op.eq]: targetId },
            },
        };
    """
    result = extractor.extract_outline(lang_key="js", content_text=js_src, budget_sec=0.2)

    assert result.degraded is False
    field_names = {str(s.get("name")) for s in result.symbols if s.get("kind") == "field"}
    assert "[Op.in]" in field_names
    assert "[Op.eq]" in field_names


def test_javascript_outline_emits_destructured_require_names_as_field_symbols() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("js"):
        return

    js_src = """
        const { Op, sequelizeLibrary } = require('sequelize');
    """
    result = extractor.extract_outline(lang_key="js", content_text=js_src, budget_sec=0.2)

    assert result.degraded is False
    field_names = {str(s.get("name")) for s in result.symbols if s.get("kind") == "field"}
    assert "Op" in field_names
    assert "sequelizeLibrary" in field_names


def test_javascript_outline_emits_catch_parameter_as_variable_symbol() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("js"):
        return

    js_src = """
        try {
            run();
        } catch (e) {
            throw e;
        }
    """
    result = extractor.extract_outline(lang_key="js", content_text=js_src, budget_sec=0.2)

    assert result.degraded is False
    variables = {str(s.get("name")) for s in result.symbols if s.get("kind") == "variable"}
    assert "e" in variables
