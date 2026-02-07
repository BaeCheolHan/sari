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
    print("❌ ERROR: tree-sitter libraries not found!")

class ASTEngine:
    def __init__(self):
        self.logger = logging.getLogger("sari.ast")
        self.registry = HandlerRegistry()
    
    @property
    def enabled(self) -> bool: return HAS_LIBS
    
    def _get_language(self, name: str) -> Any:
        if not HAS_LIBS: return None
        m = {"hcl": "hcl", "tf": "hcl", "py": "python", "js": "javascript", "ts": "typescript", "java": "java", "kt": "kotlin", "rs": "rust", "go": "go", "sh": "bash", "sql": "sql", "swift": "swift"}
        target = m.get(name.lower(), name.lower())
        try:
            return get_language(target)
        except Exception as e:
            print(f"⚠️ Warning: Could not load language {target}: {e}")
            return None

    def extract_symbols(self, path: str, language: str, content: str, tree: Any = None) -> Tuple[List[Tuple], List[Any]]:
        if not HAS_LIBS or not content: return [], []
        ext = path.split(".")[-1].lower() if "." in path else language.lower()
        
        lang_obj = self._get_language(ext)
        if not lang_obj: return [], []
        
        parser = Parser(); parser.set_language(lang_obj)
        if tree is None:
            tree = parser.parse(content.encode("utf-8", errors="ignore"))
        
        data = content.encode("utf-8", errors="ignore"); lines = content.splitlines(); symbols = []
        def get_t(n): return data[n.start_byte:n.end_byte].decode("utf-8", errors="ignore")
        def find_id(node):
            for c in node.children:
                if c.type in ("identifier", "name", "type_identifier", "constant"): return get_t(c)
            for c in node.children:
                res = find_id(c)
                if res: return res
            return None

        handler = self.registry.get_handler(ext)
        def walk(node, p_name="", p_meta=None):
            kind, name, meta, is_valid = None, None, {"annotations": []}, False
            if handler:
                kind, name, meta, is_valid = handler.handle_node(node, get_t, find_id, ext, p_meta or {})
                if is_valid and not name: name = find_id(node)
            elif node.type in ("class_declaration", "function_definition", "method_declaration", "resource"):
                kind, is_valid, name = "class", True, find_id(node)

            if is_valid and name:
                start, end = node.start_point[0] + 1, node.end_point[0] + 1
                symbols.append((path, name, kind, start, end, lines[start-1].strip() if start <= len(lines) else "", p_name, json.dumps(meta), "", name, _symbol_id(path, kind, name)))
                p_name, p_meta = name, meta
            for child in node.children: walk(child, p_name, p_meta)

        walk(tree.root_node, p_meta={}); return symbols, []
