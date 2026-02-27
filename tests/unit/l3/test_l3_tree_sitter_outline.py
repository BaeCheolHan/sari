from __future__ import annotations

import types
from pathlib import Path

from sari.services.collection.l3.l3_asset_loader import L3AssetLoader
from sari.services.collection.l3.l3_tree_sitter_outline import TreeSitterOutlineExtractor
from sari.services.collection.l3.l3_tree_sitter_outline import TreeSitterOutlineResult


def test_load_language_uses_fallback_module_loader(monkeypatch) -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._get_language = None
    extractor._language_cls = lambda capsule: ("wrapped", capsule)  # type: ignore[assignment]

    fake_module = types.SimpleNamespace(language=lambda: "capsule:python")
    monkeypatch.setattr(
        "sari.services.collection.l3.l3_tree_sitter_outline.importlib.import_module",
        lambda name: fake_module if name == "tree_sitter_python" else None,
    )

    loaded = extractor._load_language("python")

    assert loaded == ("wrapped", "capsule:python")


def test_resolve_get_language_prefers_language_pack(monkeypatch) -> None:
    extractor = TreeSitterOutlineExtractor()

    class _PackModule:
        @staticmethod
        def get_language(name: str):
            return f"pack:{name}"

    def _import_module(name: str):
        if name == "tree_sitter_language_pack":
            return _PackModule()
        raise ImportError(name)

    monkeypatch.setattr(
        "sari.services.collection.l3.l3_tree_sitter_outline.importlib.import_module",
        _import_module,
    )
    loader = extractor._resolve_get_language_loader()
    assert callable(loader)
    assert loader("python") == "pack:python"


def test_resolve_get_language_falls_back_to_tree_sitter_languages(monkeypatch) -> None:
    extractor = TreeSitterOutlineExtractor()

    class _LegacyModule:
        @staticmethod
        def get_language(name: str):
            return f"legacy:{name}"

    def _import_module(name: str):
        if name == "tree_sitter_language_pack":
            raise ImportError(name)
        if name == "tree_sitter_languages":
            return _LegacyModule()
        raise ImportError(name)

    monkeypatch.setattr(
        "sari.services.collection.l3.l3_tree_sitter_outline.importlib.import_module",
        _import_module,
    )
    loader = extractor._resolve_get_language_loader()
    assert callable(loader)
    assert loader("java") == "legacy:java"


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


def test_parse_tree_uses_incremental_old_tree_when_parse_key_repeats() -> None:
    extractor = TreeSitterOutlineExtractor()

    class _Parser:
        def __init__(self) -> None:
            self.calls: list[object | None] = []

        def parse(self, data, old_tree=None):  # noqa: ANN001
            self.calls.append(old_tree)
            return object()

    parser = _Parser()
    _ = extractor._parse_tree(  # noqa: SLF001
        normalized="python",
        parser=parser,
        content_bytes=b"class A:\n  pass\n",
        parse_key="/repo::a.py",
    )
    _ = extractor._parse_tree(  # noqa: SLF001
        normalized="python",
        parser=parser,
        content_bytes=b"class A:\n  pass\nclass B:\n  pass\n",
        parse_key="/repo::a.py",
    )

    assert len(parser.calls) == 2
    assert parser.calls[0] is None
    assert parser.calls[1] is not None


def test_parse_tree_falls_back_when_parser_has_no_old_tree_signature() -> None:
    extractor = TreeSitterOutlineExtractor()

    class _LegacyParser:
        def __init__(self) -> None:
            self.calls = 0

        def parse(self, data):  # noqa: ANN001
            self.calls += 1
            return object()

    parser = _LegacyParser()
    _ = extractor._parse_tree(  # noqa: SLF001
        normalized="python",
        parser=parser,
        content_bytes=b"def a():\n  return 1\n",
        parse_key="/repo::a.py",
    )
    _ = extractor._parse_tree(  # noqa: SLF001
        normalized="python",
        parser=parser,
        content_bytes=b"def a():\n  return 2\n",
        parse_key="/repo::a.py",
    )
    assert parser.calls == 2


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


def test_extract_outline_does_not_overwrite_method_name_with_nested_identifier_capture() -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._available = True

    class _Node:
        def __init__(self, node_type: str, line: int, text: bytes, parent=None) -> None:
            self.type = node_type
            self.start_point = (line - 1, 0)
            self.end_point = (line - 1, 1)
            self.text = text
            self.parent = parent

    method_node = _Node("method_declaration", 10, b"void doWork() { String body = \"x\"; }")
    method_name_node = _Node("identifier", 10, b"doWork", parent=method_node)
    block_node = _Node("block", 11, b"{ String body = \"x\"; }", parent=method_node)
    nested_identifier_node = _Node("identifier", 11, b"body", parent=block_node)

    class _Parser:
        def parse(self, data):  # noqa: ANN001
            return types.SimpleNamespace(root_node=object())

    extractor._get_or_create_parser = lambda normalized_lang: _Parser()  # type: ignore[assignment]
    extractor._languages["java"] = object()
    extractor._compiled_queries["java"] = object()
    extractor._query_cls = object  # type: ignore[assignment]
    extractor._run_query_captures = lambda **kwargs: {  # type: ignore[method-assign]
        "symbol.method": [method_node],
        "name": [method_name_node, nested_identifier_node],
    }

    result = extractor.extract_outline(lang_key="java", content_text="class X {}", budget_sec=0.1)

    assert result.degraded is False
    assert result.symbols
    assert result.symbols[0]["kind"] == "method"
    assert result.symbols[0]["name"] == "doWork"


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


def test_java_outline_supplements_unicode_method_names_with_regex() -> None:
    extractor = TreeSitterOutlineExtractor()
    if not extractor.is_available_for("java"):
        return

    java_src = """
        class SampleTest {
            public void setUp() {}
            public void 특가대장_포인트_사용하기() {}
            public void 특가대장_포인트_취소하기() {}
        }
    """
    result = extractor.extract_outline(lang_key="java", content_text=java_src, budget_sec=0.2)

    assert result.degraded is False
    method_names = {str(s.get("name")) for s in result.symbols if s.get("kind") == "method"}
    assert "setUp" in method_names
    assert "특가대장_포인트_사용하기" in method_names
    assert "특가대장_포인트_취소하기" in method_names


def test_query_source_prefers_packaged_tags_scm(monkeypatch, tmp_path: Path) -> None:
    extractor = TreeSitterOutlineExtractor()
    tags_path = tmp_path / "tree_sitter_java" / "queries" / "tags.scm"
    tags_path.parent.mkdir(parents=True, exist_ok=True)
    tags_path.write_text("(class_declaration name: (identifier) @name) @symbol.class", encoding="utf-8")

    fake_mod = types.SimpleNamespace(__file__=str((tmp_path / "tree_sitter_java" / "__init__.py")))
    monkeypatch.setattr(
        "sari.services.collection.l3.l3_tree_sitter_outline.importlib.import_module",
        lambda name: fake_mod if name == "tree_sitter_java" else (_ for _ in ()).throw(ImportError(name)),
    )

    source = extractor._get_query_source("java")

    assert source is not None
    assert "class_declaration" in source


def test_query_source_falls_back_to_builtin_when_packaged_tags_missing(monkeypatch) -> None:
    extractor = TreeSitterOutlineExtractor()
    monkeypatch.setattr(
        "sari.services.collection.l3.l3_tree_sitter_outline.importlib.import_module",
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


def test_query_source_reads_scala_asset_in_apply_mode(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    (assets / "queries" / "scala").mkdir(parents=True, exist_ok=True)
    (assets / "mappings").mkdir(parents=True, exist_ok=True)
    (assets / "manifest.json").write_text('{"version":"test"}', encoding="utf-8")
    (assets / "mappings" / "default.yaml").write_text("{}", encoding="utf-8")
    (assets / "queries" / "scala" / "outline.scm").write_text(
        "(class_definition name: (identifier) @name) @symbol.class",
        encoding="utf-8",
    )
    loader = L3AssetLoader(assets_root=assets)
    extractor = TreeSitterOutlineExtractor(asset_loader=loader, asset_mode="apply")

    source = extractor._get_query_source("scala")

    assert source is not None
    assert "class_definition" in source


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


def test_language_alias_maps_kotlin_extensions_to_kotlin() -> None:
    extractor = TreeSitterOutlineExtractor()

    assert extractor._LANGUAGE_ALIASES.get("kt") == "kotlin"
    assert extractor._LANGUAGE_ALIASES.get("kts") == "kotlin"


def test_compile_query_returns_none_when_language_query_raises_name_error() -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._query_cls = None  # type: ignore[assignment]

    class _Lang:
        def query(self, source):  # noqa: ANN001
            raise NameError("Invalid node type function_expression")

    assert extractor._compile_query(language=_Lang(), source="(function_expression) @name") is None


def test_compile_query_returns_none_when_language_query_raises_syntax_error() -> None:
    extractor = TreeSitterOutlineExtractor()
    extractor._query_cls = None  # type: ignore[assignment]

    class _Lang:
        def query(self, source):  # noqa: ANN001
            raise SyntaxError("Invalid syntax")

    assert extractor._compile_query(language=_Lang(), source="(broken_query)") is None


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


def test_kotlin_outline_excludes_local_declarations_and_marks_member_method() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("kt"):
        return

    kt_src = """
        class Sample {
            val field = 1

            fun member() {
                val localInMember = 1
            }
        }

        fun topLevel() {
            val localInTop = 2
        }
    """
    result = extractor.extract_outline(lang_key="kt", content_text=kt_src, budget_sec=0.3)

    assert result.degraded is False
    by_name = {str(s.get("name")): str(s.get("kind")) for s in result.symbols}
    assert by_name.get("Sample") == "class"
    assert by_name.get("field") == "field"
    assert by_name.get("member") == "method"
    assert by_name.get("topLevel") == "function"
    assert "localInMember" not in by_name
    assert "localInTop" not in by_name


def test_kotlin_outline_captures_top_level_property_and_constructor_property() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("kt"):
        return

    kt_src = """
        data class User(
            val id: Long,
            val name: String,
        )

        val users = listOf<User>()
    """
    result = extractor.extract_outline(lang_key="kt", content_text=kt_src, budget_sec=0.3)

    assert result.degraded is False
    by_name = {str(s.get("name")): str(s.get("kind")) for s in result.symbols}
    assert by_name.get("User") == "class"
    assert by_name.get("id") == "field"
    assert by_name.get("name") == "field"
    assert by_name.get("users") == "field"


def test_kotlin_outline_emits_interface_symbol() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("kt"):
        return

    kt_src = """
        interface Api {
            fun run()
        }
    """
    result = extractor.extract_outline(lang_key="kt", content_text=kt_src, budget_sec=0.3)

    assert result.degraded is False
    by_name = {str(s.get("name")): str(s.get("kind")) for s in result.symbols}
    assert by_name.get("Api") == "interface"


def test_python_outline_captures_async_function_and_method() -> None:
    extractor = TreeSitterOutlineExtractor(asset_mode="apply")
    if not extractor.is_available_for("py"):
        return

    py_src = """
class Service:
    async def run(self):
        return 1

async def main():
    return await Service().run()
"""
    result = extractor.extract_outline(lang_key="py", content_text=py_src, budget_sec=0.3)

    assert result.degraded is False
    by_name = {str(s.get("name")): str(s.get("kind")) for s in result.symbols}
    assert by_name.get("Service") == "class"
    assert by_name.get("run") == "method"
    assert by_name.get("main") == "function"


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
