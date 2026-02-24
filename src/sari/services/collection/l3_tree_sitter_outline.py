"""L3 tree-sitter 기반 outline 추출기."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import time
from typing import Any


@dataclass(frozen=True)
class TreeSitterOutlineResult:
    symbols: list[dict[str, object]]
    degraded: bool
    reason: str | None = None


class TreeSitterOutlineExtractor:
    """tree-sitter를 사용해 경량 심볼 outline을 추출한다."""

    _LANGUAGE_ALIASES = {
        "py": "python",
        "ts": "typescript",
        "java": "java",
    }
    _FALLBACK_LANGUAGE_LOADERS = {
        "python": ("tree_sitter_python", "language"),
        "java": ("tree_sitter_java", "language"),
        "typescript": ("tree_sitter_typescript", "language_typescript"),
    }
    _QUERY_LANGUAGE_PACKAGES = {
        "python": "tree_sitter_python",
        "java": "tree_sitter_java",
        "typescript": "tree_sitter_typescript",
    }

    _NODE_KIND_BY_TYPE = {
        "python": {
            "class_definition": "class",
            "function_definition": "function",
        },
        "typescript": {
            "class_declaration": "class",
            "function_declaration": "function",
            "method_definition": "method",
            "method_signature": "method",
        },
        "java": {
            "package_declaration": "module",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "annotation_type_declaration": "interface",
            "record_declaration": "class",
            "enum_declaration": "enum",
            "enum_constant": "field",
            "variable_declarator": "field",
            "method_declaration": "method",
            "constructor_declaration": "method",
        },
    }
    _QUERY_SOURCES = {
        # Library-backed strategy: let tree-sitter Query engine do the node matching,
        # then keep sari-side normalization minimal.
        "python": """
            (class_definition name: (identifier) @name) @symbol.class
            (function_definition name: (identifier) @name) @symbol.function
        """,
        "typescript": """
            (class_declaration name: (type_identifier) @name) @symbol.class
            (function_declaration name: (identifier) @name) @symbol.function
            (method_definition name: (property_identifier) @name) @symbol.method
            (method_signature name: (property_identifier) @name) @symbol.method
        """,
        "java": """
            (package_declaration (scoped_identifier) @name) @symbol.module
            (package_declaration (identifier) @name) @symbol.module
            (class_declaration name: (identifier) @name) @symbol.class
            (interface_declaration name: (identifier) @name) @symbol.interface
            (annotation_type_declaration name: (identifier) @name) @symbol.interface
            (record_declaration name: (identifier) @name) @symbol.class
            (enum_declaration name: (identifier) @name) @symbol.enum
            (method_declaration name: (identifier) @name) @symbol.method
            (constructor_declaration name: (identifier) @name) @symbol.method
            (field_declaration (variable_declarator name: (identifier) @name) @symbol.field)
            (enum_constant name: (identifier) @name) @symbol.enum_constant
        """,
    }

    def __init__(self) -> None:
        self._available = False
        self._parsers: dict[str, object] = {}
        self._languages: dict[str, object] = {}
        self._init_error_reason: str | None = None
        self._language_cls = None
        self._parser_cls = None
        self._query_cls = None
        self._query_cursor_cls = None
        self._get_language = None
        self._compiled_queries: dict[str, Any] = {}
        self._query_source_cache: dict[str, str | None] = {}
        try:
            from tree_sitter import Language, Parser  # type: ignore
        except (ImportError, RuntimeError, OSError, ValueError, TypeError) as exc:
            self._init_error_reason = f"tree_sitter_unavailable:{type(exc).__name__}"
            self._available = False
            return
        self._language_cls = Language
        self._parser_cls = Parser
        try:
            from tree_sitter import Query  # type: ignore

            self._query_cls = Query
        except (ImportError, RuntimeError, OSError, ValueError, TypeError):
            self._query_cls = None
        try:
            from tree_sitter import QueryCursor  # type: ignore

            self._query_cursor_cls = QueryCursor
        except (ImportError, RuntimeError, OSError, ValueError, TypeError):
            self._query_cursor_cls = None
        try:
            from tree_sitter_languages import get_language  # type: ignore
            self._get_language = get_language
        except (ImportError, RuntimeError, OSError, ValueError, TypeError):
            self._get_language = None
        self._available = True

    def is_available_for(self, lang_key: str) -> bool:
        if not self._available:
            return False
        normalized = self._LANGUAGE_ALIASES.get(lang_key)
        if normalized is None:
            return False
        return normalized in self._NODE_KIND_BY_TYPE

    def extract_outline(self, *, lang_key: str, content_text: str, budget_sec: float) -> TreeSitterOutlineResult:
        if not self._available:
            return TreeSitterOutlineResult(symbols=[], degraded=True, reason=self._init_error_reason or "tree_sitter_unavailable")
        normalized = self._LANGUAGE_ALIASES.get(lang_key)
        if normalized is None:
            return TreeSitterOutlineResult(symbols=[], degraded=False, reason="tree_sitter_unsupported_language")
        started_at = time.perf_counter()
        parser = self._get_or_create_parser(normalized)
        if parser is None:
            return TreeSitterOutlineResult(symbols=[], degraded=True, reason="tree_sitter_parser_init_failed")
        query_result = self._extract_outline_with_query(
            normalized=normalized,
            parser=parser,
            content_text=content_text,
            budget_sec=budget_sec,
            started_at=started_at,
        )
        if query_result is not None:
            return query_result
        return self._extract_outline_legacy(
            normalized=normalized,
            parser=parser,
            content_text=content_text,
            budget_sec=budget_sec,
            started_at=started_at,
        )

    def _extract_outline_legacy(
        self,
        *,
        normalized: str,
        parser,
        content_text: str,
        budget_sec: float,
        started_at: float,
    ) -> TreeSitterOutlineResult:
        try:
            tree = parser.parse(content_text.encode("utf-8", errors="ignore"))
        except (RuntimeError, OSError, ValueError, TypeError):
            return TreeSitterOutlineResult(symbols=[], degraded=True, reason="tree_sitter_parse_failed")
        symbols: list[dict[str, object]] = []
        node_kind_map = self._NODE_KIND_BY_TYPE[normalized]
        stack = [tree.root_node]
        while stack:
            if (time.perf_counter() - started_at) > budget_sec:
                return TreeSitterOutlineResult(symbols=symbols, degraded=True, reason="tree_sitter_budget_exceeded")
            node = stack.pop()
            kind = node_kind_map.get(node.type)
            if kind is not None:
                name = self._resolve_symbol_name(node=node, content_text=content_text)
                line = int(node.start_point[0]) + 1
                end_line = int(node.end_point[0]) + 1
                symbols.append(
                    {
                        "name": name,
                        "kind": kind,
                        "line": line,
                        "end_line": end_line,
                        "symbol_key": f"{name}:{line}",
                        "parent_symbol_key": None,
                        "depth": 0,
                        "container_name": None,
                    }
                )
            children = getattr(node, "children", None)
            if isinstance(children, list):
                for child in reversed(children):
                    stack.append(child)
        symbols = self._postprocess_symbols(normalized=normalized, symbols=symbols)
        return TreeSitterOutlineResult(symbols=symbols, degraded=False)

    def _extract_outline_with_query(
        self,
        *,
        normalized: str,
        parser,
        content_text: str,
        budget_sec: float,
        started_at: float,
    ) -> TreeSitterOutlineResult | None:
        # Query path is best-effort: if the runtime lacks Query/QueryCursor support or
        # query compilation fails, fall back to the legacy traversal extractor.
        if self._query_cls is None:
            return None
        query_source = self._get_query_source(normalized)
        if not query_source:
            return None
        language = self._languages.get(normalized)
        if language is None:
            return None
        query = self._compiled_queries.get(normalized)
        if query is None:
            query = self._compile_query(language=language, source=query_source)
            if query is None:
                return None
            self._compiled_queries[normalized] = query
        try:
            tree = parser.parse(content_text.encode("utf-8", errors="ignore"))
        except (RuntimeError, OSError, ValueError, TypeError):
            return TreeSitterOutlineResult(symbols=[], degraded=True, reason="tree_sitter_parse_failed")
        root = tree.root_node
        captures_iter = self._run_query_captures(query=query, root=root)
        if captures_iter is None:
            return None
        symbols: list[dict[str, object]] = []
        pending: dict[int, dict[str, object]] = {}
        names_by_parent_id: dict[int, tuple[str, int, int]] = {}
        capture_items = self._iter_capture_items(captures_iter, query=query)
        for item in capture_items:
            if (time.perf_counter() - started_at) > budget_sec:
                return TreeSitterOutlineResult(symbols=symbols, degraded=True, reason="tree_sitter_budget_exceeded")
            node, capture_name = self._unpack_capture_item(item, query=query)
            if node is None or not isinstance(capture_name, str):
                continue
            if capture_name == "name":
                parent = getattr(node, "parent", node)
                text = getattr(node, "text", b"")
                if isinstance(text, bytes) and text:
                    decoded = text.decode("utf-8", errors="ignore") or "anonymous"
                    name_line = int(node.start_point[0]) + 1
                    name_end_line = int(node.end_point[0]) + 1
                    names_by_parent_id[id(parent)] = (decoded, name_line, name_end_line)
                    entry = pending.get(id(parent))
                    if entry is not None:
                        entry["name"] = decoded
                        entry["line"] = name_line
                        entry["end_line"] = name_end_line
                        entry["symbol_key"] = f"{decoded}:{name_line}"
                continue
            kind = self._query_capture_to_kind(capture_name)
            if kind is None:
                continue
            line = int(node.start_point[0]) + 1
            end_line = int(node.end_point[0]) + 1
            symbol = {
                "name": self._resolve_symbol_name(node=node, content_text=content_text),
                "kind": kind,
                "line": line,
                "end_line": end_line,
                "symbol_key": f"anonymous:{line}",
                "parent_symbol_key": None,
                "depth": 0,
                "container_name": None,
            }
            symbol["symbol_key"] = f"{symbol['name']}:{line}"
            symbols.append(symbol)
            node_id = id(node)
            pending[node_id] = symbol
            pre_name = names_by_parent_id.get(node_id)
            if isinstance(pre_name, tuple):
                pre_name_text, pre_name_line, pre_name_end_line = pre_name
                if pre_name_text:
                    symbol["name"] = pre_name_text
                    symbol["line"] = pre_name_line
                    symbol["end_line"] = pre_name_end_line
                    symbol["symbol_key"] = f"{pre_name_text}:{pre_name_line}"
        if not symbols:
            return None
        symbols = self._postprocess_symbols(normalized=normalized, symbols=symbols)
        return TreeSitterOutlineResult(symbols=symbols, degraded=False)

    def _postprocess_symbols(self, *, normalized: str, symbols: list[dict[str, object]]) -> list[dict[str, object]]:
        return self._dedupe_symbols(symbols)

    def _dedupe_symbols(self, symbols: list[dict[str, object]]) -> list[dict[str, object]]:
        seen: set[tuple[str, str, int, int]] = set()
        out: list[dict[str, object]] = []
        for sym in symbols:
            try:
                key = (
                    str(sym.get("name", "")),
                    str(sym.get("kind", "other")),
                    int(sym.get("line", 0) or 0),
                    int(sym.get("end_line", 0) or 0),
                )
            except (TypeError, ValueError):
                out.append(sym)
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(sym)
        return out

    def _compile_query(self, *, language, source: str):
        # tree-sitter Python bindings differ by version:
        # - newer: Query(language, source)
        # - older: language.query(source)
        if self._query_cls is not None:
            try:
                return self._query_cls(language, source)
            except (RuntimeError, OSError, ValueError, TypeError):
                pass
        lang_query = getattr(language, "query", None)
        if callable(lang_query):
            try:
                return lang_query(source)
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                return None
        return None

    def _query_capture_to_kind(self, capture_name: str) -> str | None:
        if capture_name.startswith("symbol."):
            return capture_name.split(".", 1)[1]
        if capture_name.startswith("definition."):
            raw = capture_name.split(".", 1)[1]
            return {
                "class": "class",
                "method": "method",
                "function": "function",
                "interface": "interface",
                "enum": "enum",
                "module": "module",
                "package": "module",
                "field": "field",
                "constant": "field",
            }.get(raw, "other")
        return None

    def _iter_capture_items(self, captures_iter, *, query):
        # Newer tree-sitter Python bindings may return dict[str, list[node]]
        if isinstance(captures_iter, dict):
            flat: list[tuple[object, str]] = []
            for capture_name, nodes in captures_iter.items():
                if not isinstance(capture_name, str):
                    continue
                if not isinstance(nodes, list):
                    continue
                for node in nodes:
                    flat.append((node, capture_name))
            return flat
        return captures_iter

    def _run_query_captures(self, *, query, root):
        # Support multiple tree-sitter Python API variants.
        cursor_cls = self._query_cursor_cls
        try:
            if cursor_cls is not None:
                cursor = cursor_cls()
                captures = getattr(cursor, "captures", None)
                if callable(captures):
                    return captures(query, root)
            captures = getattr(query, "captures", None)
            if callable(captures):
                return captures(root)
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
            return None
        return None

    def _unpack_capture_item(self, item, *, query):
        # Variant A: tuple(node, "capture_name")
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], str):
            return item[0], item[1]
        # Variant B: tuple(node, capture_index)
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], int):
            names = getattr(query, "capture_names", None)
            if isinstance(names, (list, tuple)) and 0 <= item[1] < len(names):
                return item[0], str(names[item[1]])
        # Variant C: dict[str, list[node]]
        if isinstance(item, tuple) and len(item) == 2 and hasattr(item[1], "__iter__"):
            return None, None
        return None, None

    def _get_or_create_parser(self, normalized_lang: str):
        parser = self._parsers.get(normalized_lang)
        if parser is not None:
            return parser
        try:
            language = self._languages.get(normalized_lang)
            if language is None:
                language = self._load_language(normalized_lang)
                if language is None:
                    return None
                self._languages[normalized_lang] = language
            parser = self._build_parser(language)
            if parser is None:
                return None
        except (RuntimeError, OSError, ValueError, TypeError):
            return None
        self._parsers[normalized_lang] = parser
        return parser

    def _get_query_source(self, normalized_lang: str) -> str | None:
        cached = self._query_source_cache.get(normalized_lang, ...)
        if cached is not ...:
            return cached
        source = self._load_packaged_tags_query(normalized_lang)
        if source and normalized_lang == "java":
            # Packaged tags.scm is preferred, but it does not include package/field/constructor/enum
            # definitions we need for outline parity.
            source = f"{source}\n{self._QUERY_SOURCES['java']}"
        if not source:
            source = self._QUERY_SOURCES.get(normalized_lang)
        self._query_source_cache[normalized_lang] = source
        return source

    def _load_packaged_tags_query(self, normalized_lang: str) -> str | None:
        package_name = self._QUERY_LANGUAGE_PACKAGES.get(normalized_lang)
        if package_name is None:
            return None
        try:
            module = importlib.import_module(package_name)
        except (ImportError, RuntimeError, OSError, ValueError, TypeError):
            return None
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str) or not module_file:
            return None
        tags_path = Path(module_file).resolve().parent / "queries" / "tags.scm"
        try:
            if not tags_path.is_file():
                return None
            source = tags_path.read_text(encoding="utf-8")
        except (OSError, RuntimeError, ValueError):
            return None
        return source or None

    def _load_language(self, normalized_lang: str):
        if callable(self._get_language):
            try:
                return self._get_language(normalized_lang)
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                ...
        fallback = self._FALLBACK_LANGUAGE_LOADERS.get(normalized_lang)
        if fallback is None:
            return None
        module_name, attr_name = fallback
        try:
            module = importlib.import_module(module_name)
            loader = getattr(module, attr_name, None)
            if not callable(loader):
                return None
            capsule = loader()
            return self._language_cls(capsule)
        except (ImportError, RuntimeError, OSError, ValueError, TypeError, AttributeError):
            return None

    def _build_parser(self, language):
        # Prefer the explicit set_language path for compatibility across
        # tree-sitter Python bindings / prebuilt language bundles.
        parser = self._parser_cls()
        setter = getattr(parser, "set_language", None)
        if callable(setter):
            try:
                setter(language)
                return parser
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                ...
        try:
            return self._parser_cls(language)
        except TypeError:
            return None

    def _resolve_symbol_name(self, *, node, content_text: str) -> str:
        node_type = str(getattr(node, "type", ""))
        if node_type == "package_declaration":
            start_byte = int(getattr(node, "start_byte", 0))
            end_byte = int(getattr(node, "end_byte", 0))
            snippet = content_text[start_byte:end_byte].strip()
            if snippet:
                normalized = snippet.replace("package", "", 1).strip().rstrip(";").strip()
                if normalized:
                    return normalized
        by_field = getattr(node, "child_by_field_name", None)
        if callable(by_field):
            name_node = by_field("name")
            if name_node is not None:
                text = getattr(name_node, "text", b"")
                if isinstance(text, bytes) and text:
                    return text.decode("utf-8", errors="ignore") or "anonymous"
        for child in getattr(node, "children", []) or []:
            if getattr(child, "type", "") in {"identifier", "type_identifier", "property_identifier"}:
                text = getattr(child, "text", b"")
                if isinstance(text, bytes) and text:
                    return text.decode("utf-8", errors="ignore") or "anonymous"
        start_byte = int(getattr(node, "start_byte", 0))
        end_byte = int(getattr(node, "end_byte", 0))
        snippet = content_text[start_byte:end_byte].strip().splitlines()
        if snippet and snippet[0] != "":
            return snippet[0][:80]
        return "anonymous"
