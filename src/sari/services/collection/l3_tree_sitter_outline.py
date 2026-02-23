"""L3 tree-sitter 기반 outline 추출기."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import time


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
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "class",
            "method_declaration": "method",
            "constructor_declaration": "method",
        },
    }

    def __init__(self) -> None:
        self._available = False
        self._parsers: dict[str, object] = {}
        self._languages: dict[str, object] = {}
        self._init_error_reason: str | None = None
        try:
            from tree_sitter import Language, Parser  # type: ignore
        except (ImportError, RuntimeError, OSError, ValueError, TypeError) as exc:
            self._init_error_reason = f"tree_sitter_unavailable:{type(exc).__name__}"
            self._available = False
            return
        self._language_cls = Language
        self._parser_cls = Parser
        self._get_language = None
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
        return TreeSitterOutlineResult(symbols=symbols, degraded=False)

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

    def _load_language(self, normalized_lang: str):
        if callable(self._get_language):
            try:
                return self._get_language(normalized_lang)
            except (RuntimeError, OSError, ValueError, TypeError):
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
        try:
            return self._parser_cls(language)
        except TypeError:
            parser = self._parser_cls()
            setter = getattr(parser, "set_language", None)
            if not callable(setter):
                return None
            setter(language)
            return parser

    def _resolve_symbol_name(self, *, node, content_text: str) -> str:
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
