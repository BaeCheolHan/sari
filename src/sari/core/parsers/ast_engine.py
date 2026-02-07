from typing import Any, Optional, List, Tuple, Dict
import json
import logging
import re
from .common import _qualname, _symbol_id
from .handlers import HandlerRegistry

try:
    import tree_sitter
    from tree_sitter import Parser
    from tree_sitter_languages import get_language
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False

class ASTEngine:
    def __init__(self):
        self.logger = logging.getLogger("sari.ast")
        self.registry = HandlerRegistry()
    
    @property
    def enabled(self) -> bool: return HAS_LIBS
    
    def parse(self, language: str, content: str, old_tree: Any = None) -> Optional[Any]:
        if not HAS_LIBS: return None
        lang_obj = self._get_language(language)
        if not lang_obj: return None
        try:
            parser = Parser(); parser.set_language(lang_obj)
            encoded = content.encode("utf-8", errors="ignore")
            return parser.parse(encoded, old_tree) if old_tree is not None else parser.parse(encoded)
        except Exception: return None

    def _get_language(self, name: str) -> Any:
        if not HAS_LIBS: return None
        m = {"hcl": "hcl", "tf": "hcl", "py": "python", "js": "javascript", "ts": "typescript", "java": "java", "kt": "kotlin", "rs": "rust", "go": "go", "sh": "bash", "sql": "sql", "swift": "swift"}
        target = m.get(name.lower(), name.lower())
        try: return get_language(target)
        except: return None

    def extract_symbols(self, path: str, language: str, content: str, tree: Any = None) -> Tuple[List[Tuple], List[Any]]:
        if not content: return [], []
        ext = path.split(".")[-1].lower() if "." in path else language.lower()
        if ext == "xml": return self._mybatis(path, content), []
        if ext in ("md", "markdown"): return self._markdown(path, content), []
        if ext == "vue":
            m = re.search(r"<script[^>]*>\s*(.*?)\s*</script>", content, re.DOTALL)
            if m: return self.extract_symbols(path.replace(".vue", ".js"), "javascript", m.group(1))
            return [], []

        lang_obj = self._get_language(ext)
        if not lang_obj: return [], []
        
        parser = Parser(); parser.set_language(lang_obj)
        if tree is None: tree = parser.parse(content.encode("utf-8", errors="ignore"))
        data = content.encode("utf-8", errors="ignore"); lines = content.splitlines(); symbols = []

        def get_t(n): return data[n.start_byte:n.end_byte].decode("utf-8", errors="ignore")
        def get_child(n, *types):
            for c in n.children:
                if c.type in types: return c
            return None
        def find_id(node, prefer_pure_identifier=False):
            for c in node.children:
                if c.type in ("identifier", "name", "type_identifier", "constant"): return get_t(c)
            for c in node.children:
                if c.type in ("key", "property_identifier", "variable_name", "simple_identifier"): return get_t(c)
            # Recursive check for declarators/blocks
            if node.type in ("variable_declarator", "lexical_declaration", "block", "resource", "module"):
                for c in node.children:
                    res = find_id(c, prefer_pure_identifier)
                    if res: return res
            return None

        handler = self.registry.get_handler(ext)

        def walk(node, p_name="", p_meta=None):
            kind, name, meta, is_valid = None, None, {"annotations": []}, False
            n_type = node.type
            
            if handler:
                kind, name, meta, is_valid = handler.handle_node(node, get_t, find_id, ext, p_meta or {})
                if is_valid and not name: name = find_id(node)
                if is_valid and hasattr(handler, "extract_api_info"):
                    api_info = handler.extract_api_info(node, get_t, get_child)
                    if api_info.get("http_path"):
                        cp = p_meta.get("http_path", "") if p_meta else ""
                        meta["http_path"] = (cp + api_info["http_path"]).replace("//", "/")
                        meta["http_methods"] = api_info.get("http_methods", [])
            else:
                # Resilient Fallback for HCL, SQL, and others
                if n_type in ("class_declaration", "function_definition", "method_declaration", "block", "resource", "module", "create_table_statement", "class", "method"):
                    kind = "class" if any(x in n_type for x in ("class", "struct", "enum", "block", "resource", "table", "module")) else "function"
                    is_valid = True
                    # Enhanced HCL label extraction
                    if n_type in ("block", "resource", "module"):
                        labels = [get_t(c).strip('"') for c in node.children if c.type in ("identifier", "string_lit", "string_literal")]
                        if labels and labels[0] in ("resource", "variable", "module", "output", "data"): labels = labels[1:]
                        name = ".".join(labels) if labels else find_id(node)
                    else: name = find_id(node)

            if is_valid and name:
                start, end = node.start_point[0] + 1, node.end_point[0] + 1
                symbols.append((path, name, kind, start, end, lines[start-1].strip() if start <= len(lines) else "", p_name, json.dumps(meta), "", name, _symbol_id(path, kind, name)))
                p_name, p_meta = name, meta
            for child in node.children: walk(child, p_name, p_meta)

        walk(tree.root_node, p_meta={}); return symbols, []

    def _mybatis(self, path, content):
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.search(r'<(select|insert|update|delete)\s+id=["\']([^"\']+)["\']', line)
            if m: symbols.append((path, m.group(2), "method", i+1, i+1, line.strip(), "Mapper", json.dumps({"framework": "MyBatis"}), "", m.group(2), _symbol_id(path, "method", m.group(2))))
        return symbols

    def _markdown(self, path, content):
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.match(r"^(#+)\s+(.*)", line.strip())
            if m: symbols.append((path, m.group(2), "doc", i+1, i+1, line.strip(), "", json.dumps({"lvl": len(m.group(1))}), "", m.group(2), _symbol_id(path, "doc", m.group(2))))
        return symbols

    def _jsp(self, path, content):
        symbols = []
        for i, line in enumerate(content.splitlines()):
            if "<%" in line: symbols.append((path, "scriptlet", "logic", i+1, i+1, line.strip(), "", "{}", "", "jsp", _symbol_id(path, "logic", str(i))))
        return symbols
