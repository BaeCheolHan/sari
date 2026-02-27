"""L3 tree-sitter 기반 outline 추출기."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import logging
from pathlib import Path
import re
import time
from typing import Any
import warnings

from .l3_asset_loader import L3AssetLoader
from .l3_language_processor_registry import L3LanguageProcessorRegistry


@dataclass(frozen=True)
class TreeSitterOutlineResult:
    symbols: list[dict[str, object]]
    degraded: bool
    reason: str | None = None


class TreeSitterOutlineExtractor:
    """tree-sitter를 사용해 경량 심볼 outline을 추출한다."""

    _LANGUAGE_ALIASES = {
        "py": "python",
        "js": "javascript",
        "jsx": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "mjs": "javascript",
        "cjs": "javascript",
        "java": "java",
        "kt": "kotlin",
        "kts": "kotlin",
        "kotlin": "kotlin",
        "scala": "scala",
        "vue": "typescript",
    }
    _FALLBACK_LANGUAGE_LOADERS = {
        "python": ("tree_sitter_python", "language"),
        "java": ("tree_sitter_java", "language"),
        "javascript": ("tree_sitter_javascript", "language"),
        "typescript": ("tree_sitter_typescript", "language_typescript"),
        "kotlin": ("tree_sitter_kotlin", "language"),
        "scala": ("tree_sitter_scala", "language"),
    }
    _QUERY_LANGUAGE_PACKAGES = {
        "python": "tree_sitter_python",
        "java": "tree_sitter_java",
        "javascript": "tree_sitter_javascript",
        "typescript": "tree_sitter_typescript",
        "kotlin": "tree_sitter_kotlin",
        "scala": "tree_sitter_scala",
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
        "javascript": {
            "class_declaration": "class",
            "function_declaration": "function",
            "method_definition": "method",
            "lexical_declaration": "field",
            "variable_declaration": "field",
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
        "kotlin": {
            "class_declaration": "class",
            "object_declaration": "class",
            "type_alias": "class",
            "function_declaration": "function",
            "property_declaration": "field",
            "secondary_constructor": "method",
        },
        "scala": {
            "package_clause": "module",
            "class_definition": "class",
            "trait_definition": "interface",
            "object_definition": "class",
            "enum_definition": "enum",
            "function_definition": "method",
            "function_declaration": "method",
            "val_definition": "field",
            "var_definition": "field",
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
        "javascript": """
            (class_declaration name: (identifier) @name) @symbol.class
            (function_declaration name: (identifier) @name) @symbol.function
            (method_definition name: (property_identifier) @name) @symbol.method
            (lexical_declaration (variable_declarator name: (identifier) @name value: [(arrow_function) (function_expression)])) @definition.function
            (variable_declaration (variable_declarator name: (identifier) @name value: [(arrow_function) (function_expression)])) @definition.function
            (assignment_expression left: (identifier) @definition.function right: [(arrow_function) (function_expression)])
            (assignment_expression left: (member_expression property: (property_identifier) @definition.function) right: [(arrow_function) (function_expression)])
            (pair key: (property_identifier) @name value: [(arrow_function) (function_expression)]) @definition.function
            (call_expression function: (identifier) @definition.function arguments: (arguments [(arrow_function) (function_expression)]))
            (call_expression function: (member_expression property: (property_identifier) @definition.function) arguments: (arguments [(arrow_function) (function_expression)]))
            (pair key: (property_identifier) @symbol.field)
            (pair key: (string) @symbol.field)
            (pair key: (computed_property_name) @symbol.field)
            (shorthand_property_identifier) @symbol.field
            (shorthand_property_identifier_pattern) @symbol.field
            (variable_declarator name: (identifier) @name) @symbol.field
            (catch_clause parameter: (identifier) @symbol.variable)
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
        "kotlin": """
            (class_declaration (type_identifier) @name) @symbol.class
            (class_parameter (simple_identifier) @name) @symbol.field
            (source_file (function_declaration (simple_identifier) @name) @symbol.function)
            (source_file (property_declaration (variable_declaration (simple_identifier) @name) @symbol.field))
            (class_body (function_declaration (simple_identifier) @name) @symbol.method)
            (class_body (property_declaration (variable_declaration (simple_identifier) @name) @symbol.field))
        """,
        "scala": """
            (package_clause (package_identifier) @name) @symbol.module
            (class_definition name: (identifier) @name) @symbol.class
            (trait_definition name: (identifier) @name) @symbol.interface
            (object_definition name: (identifier) @name) @symbol.class
            (enum_definition name: (identifier) @name) @symbol.enum
            (function_definition name: (identifier) @name) @symbol.method
            (function_declaration name: (identifier) @name) @symbol.method
            (val_definition (identifier) @name) @symbol.field
            (var_definition (identifier) @name) @symbol.field
        """,
    }
    _JAVA_METHOD_LINE_REGEX = re.compile(
        r"^\s*(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
        r"(?:native\s+)?(?:abstract\s+)?(?:<[^>]+>\s*)?(?:[A-Za-z_$\u0080-\uffff][\w$<>\[\],?.]*?)\s+"
        r"([A-Za-z_$\u0080-\uffff][\w$\u0080-\uffff]*)\s*\([^)\n;]*\)\s*"
        r"(?:throws\s+[A-Za-z0-9_.$,\s]+)?\s*\{\s*\}?\s*$"
    )

    def __init__(
        self,
        *,
        asset_loader: L3AssetLoader | None = None,
        asset_mode: str = "shadow",
        asset_lang_allowlist: tuple[str, ...] = (),
        language_registry: L3LanguageProcessorRegistry | None = None,
    ) -> None:
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
        self._asset_loader = asset_loader or L3AssetLoader()
        self._language_registry = language_registry or L3LanguageProcessorRegistry()
        self._asset_mode = str(asset_mode or "shadow").strip().lower()
        self._asset_lang_allowlist = {
            str(item).strip().lower() for item in asset_lang_allowlist if str(item).strip() != ""
        }
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
        self._get_language = self._resolve_get_language_loader()
        self._available = True
        self._logger = logging.getLogger(__name__)

    def _resolve_get_language_loader(self):
        """tree-sitter language loader를 우선순위에 따라 해석한다.

        우선순위:
        1) tree_sitter_language_pack.get_language (신규 경로)
        2) tree_sitter_languages.get_language (레거시 폴백)
        """
        for module_name in ("tree_sitter_language_pack", "tree_sitter_languages"):
            try:
                module = importlib.import_module(module_name)
            except (ImportError, RuntimeError, OSError, ValueError, TypeError):
                continue
            get_language = getattr(module, "get_language", None)
            if callable(get_language):
                return get_language
        return None

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
        language_processor = self._language_registry.resolve_by_pattern_key(pattern_key=normalized)
        parser = self._get_or_create_parser(normalized)
        if parser is None:
            return TreeSitterOutlineResult(symbols=[], degraded=True, reason="tree_sitter_parser_init_failed")
        query_result = self._extract_outline_with_query(
            normalized=normalized,
            parser=parser,
            content_text=content_text,
            budget_sec=budget_sec,
            language_processor=language_processor,
        )
        if query_result is not None:
            return query_result
        return self._extract_outline_legacy(
            normalized=normalized,
            parser=parser,
            content_text=content_text,
            budget_sec=budget_sec,
        )

    def _extract_outline_legacy(
        self,
        *,
        normalized: str,
        parser,
        content_text: str,
        budget_sec: float,
    ) -> TreeSitterOutlineResult:
        try:
            tree = parser.parse(content_text.encode("utf-8", errors="ignore"))
        except (RuntimeError, OSError, ValueError, TypeError):
            return TreeSitterOutlineResult(symbols=[], degraded=True, reason="tree_sitter_parse_failed")
        started_at = time.perf_counter()
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
        symbols = self._postprocess_symbols(normalized=normalized, symbols=symbols, content_text=content_text)
        return TreeSitterOutlineResult(symbols=symbols, degraded=False)

    def _extract_outline_with_query(
        self,
        *,
        normalized: str,
        parser,
        content_text: str,
        budget_sec: float,
        language_processor,
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
            query = self._compile_query_candidates(
                language=language,
                normalized=normalized,
                primary_source=query_source,
            )
            if query is None:
                return None
            self._compiled_queries[normalized] = query
        try:
            tree = parser.parse(content_text.encode("utf-8", errors="ignore"))
        except (RuntimeError, OSError, ValueError, TypeError):
            return TreeSitterOutlineResult(symbols=[], degraded=True, reason="tree_sitter_parse_failed")
        root = tree.root_node
        started_at = time.perf_counter()
        captures_iter = self._run_query_captures(query=query, root=root)
        if captures_iter is None:
            return None
        symbols: list[dict[str, object]] = []
        pending: dict[tuple[int, int, str], dict[str, object]] = {}
        names_by_parent_id: dict[tuple[int, int, str], str] = {}
        capture_items = self._iter_capture_items(captures_iter, query=query)
        for item in capture_items:
            if (time.perf_counter() - started_at) > budget_sec:
                return TreeSitterOutlineResult(symbols=symbols, degraded=True, reason="tree_sitter_budget_exceeded")
            node, capture_name = self._unpack_capture_item(item, query=query)
            if node is None or not isinstance(capture_name, str):
                continue
            if capture_name == "name":
                parent = getattr(node, "parent", None)
                if parent is None:
                    continue
                text = getattr(node, "text", b"")
                if isinstance(text, bytes) and text:
                    decoded = text.decode("utf-8", errors="ignore") or "anonymous"
                    if len(decoded) >= 2 and decoded[0] == decoded[-1] and decoded[0] in {"'", '"', "`"}:
                        decoded = decoded[1:-1]
                    current = parent
                    climb_depth = 0
                    while current is not None:
                        current_id = self._node_identity(current)
                        if current_id not in names_by_parent_id:
                            names_by_parent_id[current_id] = decoded
                        entry = pending.get(current_id)
                        if entry is not None:
                            current_name = str(entry.get("name", "")).strip()
                            if language_processor.should_replace_symbol_name(
                                current_name=current_name,
                                candidate_name=decoded,
                                symbol_kind=str(entry.get("kind", "")),
                                symbol_node_type=str(getattr(current, "type", "")),
                                name_parent_node_type=str(getattr(parent, "type", "")),
                                climb_depth=climb_depth,
                            ):
                                entry["name"] = decoded
                                try:
                                    line_value = int(entry.get("line", 0) or 0)
                                except (TypeError, ValueError):
                                    line_value = 0
                                entry["symbol_key"] = f"{decoded}:{line_value}"
                            break
                        if not language_processor.allows_name_capture_climb(
                            parent_node_type=str(getattr(current, "type", "")),
                            climb_depth=climb_depth,
                        ):
                            break
                        current = getattr(current, "parent", None)
                        climb_depth += 1
                continue
            kind = self._query_capture_to_kind(capture_name, normalized=normalized)
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
            node_id = self._node_identity(node)
            pending[node_id] = symbol
            pre_name = names_by_parent_id.get(node_id)
            if isinstance(pre_name, str) and pre_name:
                symbol["name"] = pre_name
                symbol["symbol_key"] = f"{pre_name}:{line}"
        if not symbols:
            return None
        symbols = self._postprocess_symbols(normalized=normalized, symbols=symbols, content_text=content_text)
        return TreeSitterOutlineResult(symbols=symbols, degraded=False)

    def _compile_query_candidates(self, *, language, normalized: str, primary_source: str):
        """자산/패키지 쿼리가 깨진 경우 builtin 쿼리까지 순차 컴파일한다."""
        candidates: list[str] = []
        if isinstance(primary_source, str) and primary_source.strip() != "":
            candidates.append(primary_source)
        builtin = self._QUERY_SOURCES.get(normalized)
        if isinstance(builtin, str) and builtin.strip() != "" and builtin not in candidates:
            candidates.append(builtin)
        for source in candidates:
            compiled = self._compile_query(language=language, source=source)
            if compiled is not None:
                return compiled
        return None

    def _postprocess_symbols(
        self,
        *,
        normalized: str,
        symbols: list[dict[str, object]],
        content_text: str,
    ) -> list[dict[str, object]]:
        if normalized == "java":
            symbols = self._supplement_java_unicode_methods(symbols=symbols, content_text=content_text)
        if normalized == "kotlin":
            lines = content_text.splitlines()
            for sym in symbols:
                kind = str(sym.get("kind", ""))
                if kind != "class":
                    continue
                line_value = sym.get("line")
                if not isinstance(line_value, int) or line_value <= 0:
                    continue
                idx = line_value - 1
                if idx >= len(lines):
                    continue
                declaration_line = lines[idx].strip().lower()
                # Kotlin grammar exposes interface declarations as class_declaration.
                if declaration_line.startswith("interface "):
                    sym["kind"] = "interface"
        return self._dedupe_symbols(symbols)

    def _supplement_java_unicode_methods(
        self,
        *,
        symbols: list[dict[str, object]],
        content_text: str,
    ) -> list[dict[str, object]]:
        out = list(symbols)
        keywords = {"if", "for", "while", "switch", "catch", "return", "new", "throw"}
        # Keep supplemental scan line-based so one match cannot consume multiple method headers.
        for idx, line_text in enumerate(content_text.splitlines(), start=1):
            match = self._JAVA_METHOD_LINE_REGEX.match(line_text)
            if match is None:
                continue
            method_name = str(match.group(1) or "").strip()
            if method_name == "" or method_name in keywords:
                continue
            # Supplemental scan is only for unicode identifiers that tree-sitter-java
            # can miss in 일부 테스트 코드 스타일.
            if all(ord(ch) <= 127 for ch in method_name):
                continue
            out.append(
                {
                    "name": method_name,
                    "kind": "method",
                    "line": idx,
                    "end_line": idx,
                    "symbol_key": f"{method_name}:{idx}",
                    "parent_symbol_key": None,
                    "depth": 0,
                    "container_name": None,
                }
            )
        return out

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
        primary_error: Exception | None = None
        if self._query_cls is not None:
            try:
                return self._query_cls(language, source)
            except (RuntimeError, OSError, ValueError, TypeError, NameError, SyntaxError) as exc:
                primary_error = exc
        lang_query = getattr(language, "query", None)
        if callable(lang_query):
            try:
                return lang_query(source)
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError, NameError, SyntaxError) as fallback_exc:
                if primary_error is not None:
                    self._logger.debug(
                        "tree-sitter query compile failed on both APIs: primary=%s fallback=%s",
                        type(primary_error).__name__,
                        type(fallback_exc).__name__,
                        exc_info=True,
                    )
                return None
        if primary_error is not None:
            self._logger.debug(
                "tree-sitter primary query compile failed and fallback API is unavailable: %s",
                type(primary_error).__name__,
                exc_info=True,
            )
        return None

    def _query_capture_to_kind(self, capture_name: str, *, normalized: str) -> str | None:
        asset_map = self._asset_loader.load(normalized).capture_to_kind
        mapped = asset_map.get(capture_name)
        if mapped is not None and mapped.strip() != "":
            return mapped.strip()
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
                try:
                    cursor = cursor_cls(query)
                except TypeError:
                    cursor = cursor_cls()
                captures = getattr(cursor, "captures", None)
                if callable(captures):
                    try:
                        return captures(query, root)
                    except TypeError:
                        return captures(root)
            captures = getattr(query, "captures", None)
            if callable(captures):
                return captures(root)
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
            self._logger.debug("tree-sitter captures API failed", exc_info=True)
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
            self._logger.debug("failed to initialize parser for language=%s", normalized_lang, exc_info=True)
            return None
        self._parsers[normalized_lang] = parser
        return parser

    def _get_query_source(self, normalized_lang: str) -> str | None:
        cached = self._query_source_cache.get(normalized_lang, ...)
        if cached is not ...:
            return cached
        use_asset_query = self._asset_mode == "apply"
        if self._asset_lang_allowlist and normalized_lang not in self._asset_lang_allowlist:
            use_asset_query = False
        source: str | None = None
        asset_query: str | None = None
        if use_asset_query:
            asset_query = self._asset_loader.load(normalized_lang).query_source
            source = asset_query
        if not source:
            source = self._load_packaged_tags_query(normalized_lang)
        if source and normalized_lang == "java" and source != asset_query:
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
            self._logger.debug("failed to import packaged query module=%s", package_name, exc_info=True)
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
            self._logger.debug("failed to read packaged tags query=%s", tags_path, exc_info=True)
            return None
        return source or None

    def _load_language(self, normalized_lang: str):
        if callable(self._get_language):
            try:
                # tree_sitter_languages 내부가 구형 Language(path, name) 경로를
                # 사용할 때 FutureWarning을 발생시키므로 로컬 범위에서만 억제한다.
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message=r"Language\(path, name\) is deprecated\..*",
                        category=FutureWarning,
                    )
                    return self._get_language(normalized_lang)
            except (LookupError, RuntimeError, OSError, ValueError, TypeError, AttributeError):
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
            self._logger.debug("failed to load tree-sitter language=%s", normalized_lang, exc_info=True)
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
                self._logger.debug("parser.set_language failed; trying constructor fallback", exc_info=True)
        try:
            return self._parser_cls(language)
        except TypeError:
            self._logger.debug("parser constructor(language) is unavailable", exc_info=True)
            return None

    def _resolve_symbol_name(self, *, node, content_text: str) -> str:
        def _sanitize(raw: str) -> str:
            value = raw.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"', "`"}:
                value = value[1:-1]
            return value

        node_type = str(getattr(node, "type", ""))
        if node_type == "package_declaration":
            start_byte = int(getattr(node, "start_byte", 0))
            end_byte = int(getattr(node, "end_byte", 0))
            snippet = content_text[start_byte:end_byte].strip()
            if snippet:
                normalized = snippet.replace("package", "", 1).strip().rstrip(";").strip()
                if normalized:
                    return _sanitize(normalized)
        by_field = getattr(node, "child_by_field_name", None)
        if callable(by_field):
            name_node = by_field("name")
            if name_node is not None:
                text = getattr(name_node, "text", b"")
                if isinstance(text, bytes) and text:
                    return _sanitize(text.decode("utf-8", errors="ignore") or "anonymous")
        for child in getattr(node, "children", []) or []:
            if getattr(child, "type", "") in {"identifier", "type_identifier", "property_identifier", "simple_identifier"}:
                text = getattr(child, "text", b"")
                if isinstance(text, bytes) and text:
                    return _sanitize(text.decode("utf-8", errors="ignore") or "anonymous")
        node_text = getattr(node, "text", b"")
        if isinstance(node_text, bytes) and node_text:
            decoded = _sanitize(node_text.decode("utf-8", errors="ignore"))
            if decoded:
                return decoded
        start_byte = int(getattr(node, "start_byte", 0))
        end_byte = int(getattr(node, "end_byte", 0))
        snippet = content_text[start_byte:end_byte].strip().splitlines()
        if snippet and snippet[0] != "":
            return _sanitize(snippet[0][:80])
        return "anonymous"

    def _node_identity(self, node: object) -> tuple[int, int, str]:
        try:
            start = int(getattr(node, "start_byte", 0))
        except (TypeError, ValueError):
            start = 0
        try:
            end = int(getattr(node, "end_byte", 0))
        except (TypeError, ValueError):
            end = 0
        return (start, end, str(getattr(node, "type", "")))
