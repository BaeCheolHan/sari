import logging
import re
from typing import List, Tuple, Optional
from .handlers import HandlerRegistry
from .special_parsers import SpecialParser
import hashlib
from sari.core.models import ParseResult, ParserSymbol, ParserRelation

try:
    from tree_sitter import Parser, Language
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False


def _symbol_id(path: str, kind: str, name: str) -> str:
    h = hashlib.sha256(f"{path}:{kind}:{name}".encode()).hexdigest()
    return h


def _qualname(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name


class ASTEngine:
    """
    Tree-sitter를 사용하여 소스 코드를 구문 분석하고 구조적 심볼 정보를 추출하는 핵심 엔진입니다.
    지원되는 언어에 대해 AST(Abstract Syntax Tree) 기반의 정밀한 분석을 수행하며,
    지원되지 않는 경우 정규식 기반의 폴백(Fallback) 메카니즘을 작동시킵니다.
    """

    def __init__(self):
        self.logger = logging.getLogger("sari.ast")
        self.registry = HandlerRegistry()

    @property
    def enabled(self) -> bool:
        """Tree-sitter 라이브러리가 설치되어 실행 가능한지 여부를 반환합니다."""
        return HAS_LIBS

    def _get_language(self, name: str) -> object:
        """
        확장자 또는 언어 이름을 기반으로 Tree-sitter Language 객체를 로드합니다.
        지원되지 않는 언어이거나 라이브러리 로드 실패 시 None을 반환합니다.
        """
        if self.logger and self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                "AST engine lookup (HAS_LIBS=%s, name=%s)",
                HAS_LIBS,
                name)
        if not HAS_LIBS:
            return None
        # Normalization map
        m = {
            "hcl": "hcl",
            "tf": "hcl",
            "terraform": "hcl",
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "jsx": "javascript",
            "tsx": "typescript",
            "java": "java",
            "kt": "kotlin",
            "rs": "rust",
            "go": "go",
            "sh": "bash",
            "sql": "sql",
            "swift": "swift",
            "vue": "vue",
            "xml": "xml",
            "php": "php",
            "ruby": "ruby",
            "yaml": "yaml",
            "cs": "c_sharp",
            "rb": "ruby",
            "yml": "yaml"}
        target = m.get(name.lower(), name.lower())

        # Try individual packages (modern tree-sitter ^0.23.0 style)
        try:
            if target == "swift":
                import tree_sitter_swift
                return Language(tree_sitter_swift.language())
            elif target == "kotlin":
                import tree_sitter_kotlin
                return Language(tree_sitter_kotlin.language())
            elif target == "ruby":
                import tree_sitter_ruby
                return Language(tree_sitter_ruby.language())
            elif target == "yaml":
                import tree_sitter_yaml
                return Language(tree_sitter_yaml.language())
            elif target == "python":
                import tree_sitter_python
                return Language(tree_sitter_python.language())
            elif target == "javascript":
                import tree_sitter_javascript
                return Language(tree_sitter_javascript.language())
            elif target == "typescript":
                import tree_sitter_typescript
                return Language(tree_sitter_typescript.language_typescript())
            elif target == "go":
                import tree_sitter_go
                return Language(tree_sitter_go.language())
            elif target == "rust":
                import tree_sitter_rust
                return Language(tree_sitter_rust.language())
            elif target == "java":
                import tree_sitter_java
                return Language(tree_sitter_java.language())
            elif target == "php":
                import tree_sitter_php
                return Language(tree_sitter_php.language_php())
            elif target == "bash":
                import tree_sitter_bash
                return Language(tree_sitter_bash.language())
        except Exception as e:
            if self.logger:
                self.logger.debug(
                    "Failed to load parser for %s: %s", target, e)

        return None

    def parse(self, language: str, content: str,
              old_tree: object = None) -> Optional[object]:
        """
        주어진 언어와 소스 코드 내용을 AST Tree로 파싱합니다.
        Incremental parsing(old_tree)을 지원하여 성능을 최적화할 수 있습니다.
        """
        if not HAS_LIBS:
            return None
        lang_obj = self._get_language(language)
        if not lang_obj:
            return None
        try:
            parser = Parser(lang_obj)
        except Exception:
            return None
        encoded_content = content.encode("utf-8", errors="ignore")
        if old_tree is not None:
            return parser.parse(encoded_content, old_tree)
        return parser.parse(encoded_content)

    def extract_symbols(self,
                        path: str,
                        language: str,
                        content: str,
                        tree: object = None) -> ParseResult:
        """
        소스 코드에서 심볼(클래스, 함수, 메서드 등) 정보를 추출합니다.
        1. 특수 파서(Dockerfile 등) 시도
        2. AST 기반 정밀 파싱 시도
        3. 실패 시 정규식 기반 범용 파서(fallback) 시도
        """
        if not content:
            return ParseResult()

        ext = path.split(".")[-1].lower() if "." in path else language.lower()

        # Try special parsers first
        special_result = self._try_special_parsers(path, ext, content)
        if special_result:
            return special_result

        # Get language object and handler
        lang_obj = self._get_language(ext)
        handler = self.registry.get_handler(ext)

        # Fallback to generic regex parser if no AST support
        if not lang_obj:
            return self._try_generic_fallback(path, ext, content)

        # Parse tree if not provided
        if tree is None:
            tree = self.parse(ext, content)
        if not tree:
            return ParseResult()

        # Extract symbols from tree
        return self._extract_from_tree(path, content, tree, handler, ext)

    def _try_special_parsers(self, path: str, ext: str,
                             content: str) -> Optional[ParseResult]:
        """Try special parsers for non-AST languages."""
        # Dockerfile
        if ext in ("dockerfile", "docker") or path.lower() == "dockerfile":
            return ParseResult(
                symbols=SpecialParser.parse_dockerfile(path, content),
                relations=[],
            )

        # MyBatis XML
        if ext == "xml" and ("<mapper" in content or "<sqlMap" in content):
            return ParseResult(
                symbols=SpecialParser.parse_mybatis(path, content),
                relations=[],
            )

        # Markdown
        if ext in ("md", "markdown"):
            return ParseResult(
                symbols=SpecialParser.parse_markdown(path, content),
                relations=[],
            )

        # Vue (extract script section and parse as JavaScript)
        if ext == "vue":
            return self._parse_vue_file(path, content)

        return None

    def _parse_vue_file(self,
                        path: str,
                        content: str) -> ParseResult:
        """Extract and parse script section from Vue file."""
        m = re.search(r"<script[^>]*>\s*(.*?)\s*</script>", content, re.DOTALL)
        script_content = m.group(1) if m else ""
        if script_content:
            # Delegate to JS parser but keep original path context
            parse_result = self.extract_symbols(
                path.replace(".vue", ".js"), "javascript", script_content)
            # Fix paths back to original .vue path
            for s in parse_result.symbols:
                s.path = path
            for _ in parse_result.relations:
                # to_path is empty by default, but if it was set, we don't know where it points.
                # However, for internal relations, we might need to fix them.
                pass
            return parse_result
        return ParseResult()

    def _try_generic_fallback(
            self, path: str, ext: str, content: str) -> ParseResult:
        """Fallback to GenericRegexParser if AST not available."""
        try:
            from .factory import ParserFactory
            from .generic import GenericRegexParser
            # ParserFactory expects extension with dot
            p_ext = ext if ext.startswith(".") else f".{ext}"
            parser = ParserFactory.get_parser(p_ext)
            if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug(
                    "AST fallback: ext=%s p_ext=%s parser=%s", ext, p_ext, parser)
            if isinstance(parser, GenericRegexParser):
                if isinstance(content, bytes):
                    text_content = content.decode("utf-8", errors="ignore")
                else:
                    text_content = content
                return parser.extract(path, text_content)
        except ImportError:
            if self.logger and self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug("AST fallback import error")
        return ParseResult()

    def _extract_from_tree(self,
                           path: str,
                           content: str,
                           tree: object,
                           handler: object,
                           ext: str) -> ParseResult:
        """Extract symbols by walking the AST tree."""
        data = content.encode("utf-8", errors="ignore")
        lines = content.splitlines()
        symbols: List[ParserSymbol] = []
        relations: List[ParserRelation] = []

        # Helper functions for tree traversal
        def get_t(n):
            return data[n.start_byte:n.end_byte].decode(
                "utf-8", errors="ignore")

        def get_child(n, *types):
            for c in n.children:
                if c.type in types:
                    return c
            return None

        def find_id(node, prefer_pure_identifier=False):
            """
            AST 노드 내에서 심볼의 식별자(이름)를 찾아냅니다.
            """
            for c in node.children:
                if c.type == "identifier":
                    return get_t(c)
            if not prefer_pure_identifier:
                for c in node.children:
                    if c.type in (
                        "name",
                        "type_identifier",
                        "constant",
                        "simple_identifier",
                        "variable_name",
                            "property_identifier"):
                        return get_t(c)
            if not prefer_pure_identifier:
                for c in node.children:
                    if c.type in ("modifiers", "annotation", "parameter_list"):
                        continue
                    res = find_id(c, True)
                    if res:
                        return res
            return None

        # Context for relations (tracking current scope)
        stack: List[ParserSymbol] = []

        def walk(node, p_name="", p_meta=None):
            """
            AST 트리를 재귀적으로 순회하며 심볼 정보를 수집합니다.
            """
            # 1. Symbol Extraction
            symbol, is_new_scope = self._extract_symbol_from_node(
                node, handler, p_name, p_meta, lines, path, ext)

            if symbol:
                symbols.append(symbol)
                stack.append(symbol)
                # Update parent context for children
                p_name, p_meta = symbol.name, symbol.meta

            # 2. Relation Extraction
            if handler:
                self._extract_relations_from_node(
                    node, handler, path, stack, relations)

            # Recurse
            for child in node.children:
                walk(child, p_name, p_meta)

            if symbol:
                stack.pop()

        walk(tree.root_node, p_meta={})
        return ParseResult(symbols=symbols, relations=relations)

    def _extract_symbol_from_node(self,
                                  node,
                                  handler,
                                  p_name,
                                  p_meta,
                                  lines,
                                  path,
                                  ext) -> Tuple[Optional[ParserSymbol],
                                                bool]:
        """Handles symbol extraction for a single node."""
        kind, name, meta, is_valid = None, None, {"annotations": []}, False
        n_type = node.type

        # Use content encoding from the tree's text source if possible
        # In tree-sitter python, we typically work with the original bytes
        # We need the data to decode text from nodes

        if handler:
            # Try language-specific handler
            def get_t(n):
                # Node.text is available in newer tree-sitter, but for safety:
                return n.text.decode(
                    "utf-8",
                    errors="ignore") if hasattr(
                    n,
                    "text") else ""

            def find_id(n, prefer_pure_identifier=False):
                return self._find_id_logic(n, get_t, prefer_pure_identifier)

            kind, name, meta, is_valid = handler.handle_node(
                node, get_t, find_id, ext, p_meta or {})

            if is_valid:
                if hasattr(handler, "extract_api_info"):
                    api_info = handler.extract_api_info(
                        node,
                        get_t,
                        lambda n,
                        *
                        t: next(
                            (c for c in n.children if c.type in t),
                            None))
                    if api_info.get("http_path"):
                        parent_path = p_meta.get(
                            "http_path", "") if p_meta else ""
                        meta["http_path"] = (
                            parent_path +
                            api_info["http_path"]).replace(
                            "//",
                            "/")
                        meta["http_methods"] = api_info.get("http_methods", [])
                        meta["api"] = True
                if not name:
                    name = find_id(node)

        # Universal Fallback for common patterns
        if not is_valid and not handler:
            if n_type in (
                "class_declaration",
                "function_definition",
                "method_declaration",
                "function_item",
                "struct_item",
                "resource",
                "module",
                "variable",
                "output",
                    "create_table_statement"):
                kind = "class" if any(
                    x in n_type for x in (
                        "class",
                        "struct",
                        "enum",
                        "resource",
                        "table",
                        "module")) else "method"
                is_valid = True
                name = self._find_name_fallback(node, n_type)

        if is_valid and name and name != "unknown":
            start, end = node.start_point[0] + 1, node.end_point[0] + 1
            line_content = lines[start -
                                 1].strip() if start <= len(lines) else ""
            sid = _symbol_id(path, kind, name)
            qual = _qualname(p_name, name)

            return ParserSymbol(
                sid=sid, path=path, name=name, kind=kind,
                line=start, end_line=end, content=line_content,
                parent=p_name, meta=meta, qualname=qual
            ), True

        return None, False

    def _extract_relations_from_node(
            self, node, handler, path, stack, relations):
        """Handles relation extraction for a single node."""
        def get_t(n):
            return n.text.decode(
                "utf-8",
                errors="ignore") if hasattr(
                n,
                "text") else ""

        def find_id(n, p=False): return self._find_id_logic(n, get_t, p)

        ctx = {"get_t": get_t, "find_id": find_id, "path": path}
        if stack:
            ctx["parent_name"], ctx["parent_sid"] = stack[-1].name, stack[-1].sid

        extracted_rels = handler.handle_relation(node, ctx)
        if extracted_rels:
            for rel in extracted_rels:
                if stack:
                    rel.from_name = stack[-1].name
                    rel.from_sid = stack[-1].sid
                relations.append(rel)

    def _find_id_logic(self, node, get_t, prefer_pure_identifier=False):
        for c in node.children:
            if c.type == "identifier":
                return get_t(c)
        if not prefer_pure_identifier:
            for c in node.children:
                if c.type in (
                    "name",
                    "type_identifier",
                    "constant",
                    "simple_identifier",
                    "variable_name",
                        "property_identifier"):
                    return get_t(c)
        if not prefer_pure_identifier:
            for c in node.children:
                if c.type in ("modifiers", "annotation", "parameter_list"):
                    continue
                res = self._find_id_logic(c, get_t, True)
                if res:
                    return res
        return None

    def _find_name_fallback(self, node, n_type):
        def get_t(n):
            return n.text.decode(
                "utf-8",
                errors="ignore") if hasattr(
                n,
                "text") else ""
        if n_type in ("block", "resource", "module"):
            labels = [
                get_t(c).strip('"') for c in node.children if c.type in (
                    "identifier",
                    "string_lit",
                    "string_literal")]
            if labels and labels[0] in (
                "resource",
                "variable",
                "module",
                "output",
                    "data"):
                labels = labels[1:]
            return ".".join(
                labels) if labels else self._find_id_logic(node, get_t)
        return self._find_id_logic(node, get_t)
