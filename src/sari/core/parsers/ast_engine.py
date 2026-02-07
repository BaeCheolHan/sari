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
    """
    ASTEngine V26 - Final Resilient & Modular Expert Engine
    """
    
    def __init__(self):
        self.logger = logging.getLogger("sari.ast")
        self.registry = HandlerRegistry()
    
    @property
    def enabled(self) -> bool:
        return HAS_LIBS
    
    def parse(self, language: str, content: str, old_tree: Any = None) -> Optional[Any]:
        """Parse content and return the tree."""
        if not HAS_LIBS: return None
        lang_obj = self._get_language(language)
        if not lang_obj: return None
        try:
            parser = Parser()
            parser.set_language(lang_obj)
            encoded = content.encode("utf-8", errors="ignore")
            if old_tree is not None:
                return parser.parse(encoded, old_tree)
            return parser.parse(encoded)
        except Exception:
            return None

    def _get_language(self, name: str) -> Any:
        if not HAS_LIBS: return None
        m = {
            "hcl": "hcl", "tf": "hcl", "terraform": "hcl",
            "py": "python", "js": "javascript", "jsx": "javascript", 
            "ts": "typescript", "tsx": "typescript",
            "java": "java", "kt": "kotlin", "rs": "rust", "go": "go",
            "sh": "bash", "sql": "sql", "cs": "c_sharp", "swift": "swift",
            "rb": "ruby", "ruby": "ruby", "yaml": "yaml", "yml": "yaml"
        }
        target = m.get(name.lower(), name.lower())
        try: return get_language(target)
        except: return None

    def extract_symbols(self, path: str, language: str, content: str, tree: Any = None) -> Tuple[List[Tuple], List[Any]]:
        if not content: return [], []
        ext = path.split(".")[-1].lower() if "." in path else language.lower()
        
        if ext == "xml": return self._mybatis(path, content), []
        if ext in ("md", "markdown"): return self._markdown(path, content), []
        if ext == "jsp": return self._jsp(path, content), []
        if ext == "vue":
            m = re.search(r"<script[^>]*>\s*(.*?)\s*</script>", content, re.DOTALL)
            script_content = m.group(1) if m else ""
            if script_content:
                return self.extract_symbols(path.replace(".vue", ".js"), "javascript", script_content)
            return [], []

        lang_obj = self._get_language(ext)
        if not lang_obj: return [], []
        
        parser = Parser()
        parser.set_language(lang_obj)
        if tree is None:
            tree = parser.parse(content.encode("utf-8", errors="ignore"))
        data = content.encode("utf-8", errors="ignore")
        lines = content.splitlines()
        symbols = []

        def get_t(n): return data[n.start_byte:n.end_byte].decode("utf-8", errors="ignore")
        def get_child(n, *types):
            for c in n.children:
                if c.type in types: return c
            return None
        def find_id(node, prefer_pure_identifier=False):
            # 1. Broad Identifier matching (Kotlin/Java/TS/etc)
            target_types = ("identifier", "simple_identifier", "type_identifier", "constant")
            for c in node.children:
                if c.type in target_types: return get_t(c)
            
            # YAML-specific or key-like nodes
            for c in node.children:
                if c.type in ("key", "property_identifier", "variable_name"):
                    return get_t(c)
            
            # 2. Recursive Search for name-like nodes
            # We limit types to avoid catching parameter names or other junk
            for c in node.children:
                if c.type in ("name", "type_identifier", "constant", "variable_name", "property_identifier", "key"):
                    return get_t(c)
                # If it's a declarator, go one level deeper
                if c.type in ("variable_declarator", "lexical_declaration", "class_declaration", "method_declaration", "block_mapping_pair"):
                    res = find_id(c, prefer_pure_identifier)
                    if res: return res
            return None

        handler = self.registry.get_handler(ext)

        def walk(node, p_name="", p_meta=None):
            kind, name, meta, is_valid = None, None, {"annotations": []}, False
            n_type = node.type
            
            if handler:
                kind, name, meta, is_valid = handler.handle_node(node, get_t, find_id, ext, p_meta or {})
                if is_valid and hasattr(handler, "extract_api_info"):
                    api_info = handler.extract_api_info(node, get_t, get_child)
                    if api_info["http_path"]:
                        class_path = p_meta.get("http_path", "") if p_meta else ""
                        meta["http_path"] = (class_path + api_info["http_path"]).replace("//", "/")
                        meta["http_methods"] = api_info["http_methods"]
            else:
                # Universal Fallback for any language (HCL, SQL, etc)
                if n_type in ("class_declaration", "function_definition", "method_declaration", "function_item", "struct_item", "block", "resource", "module", "variable", "output", "create_table_statement", "class", "method", "block_mapping_pair"):
                    kind = "class" if any(x in n_type for x in ("class", "struct", "enum", "block", "resource", "table", "module", "mapping")) else "function"
                    is_valid = True
                    # Enhanced HCL label extraction: resource "aws_vpc" "main" -> "aws_vpc.main"
                    if n_type in ("block", "resource", "module"):
                        labels = [get_t(c).strip('"') for c in node.children if c.type in ("identifier", "string_lit", "string_literal")]
                        # Remove keywords like 'resource', 'variable'
                        if labels and labels[0] in ("resource", "variable", "module", "output", "data"):
                            labels = labels[1:]
                        name = ".".join(labels) if labels else find_id(node)
                    else:
                        name = find_id(node)

            if is_valid:
                if not name: name = "unknown"
                start = node.start_point[0] + 1
                symbols.append((
                    path, name, kind, start, node.end_point[0] + 1,
                    lines[start-1].strip() if start <= len(lines) else "",
                    p_name, json.dumps(meta), "", name, _symbol_id(path, kind, name)
                ))
                p_name, p_meta = name, meta
            
            for child in node.children:
                walk(child, p_name, p_meta)

        walk(tree.root_node, p_meta={})
        return symbols, []

    def _mybatis(self, path, content):
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.search(r'<(select|insert|update|delete)\s+id=["\']([^"\']+)["\']', line)
            if m: 
                meta = {"framework": "MyBatis", "mybatis_op": m.group(1)}
                symbols.append((path, m.group(2), "method", i+1, i+1, line.strip(), "Mapper", json.dumps(meta), "", m.group(2), _symbol_id(path, "method", m.group(2))))
        return symbols

    def _jsp(self, path, content):
        symbols = []
        for i, line in enumerate(content.splitlines()):
            if "<%" in line:
                symbols.append((path, f"scriptlet-{i+1}", "code", i+1, i+1, line.strip(), "", json.dumps({"type": "jsp"}), "", f"scriptlet-{i+1}", _symbol_id(path, "code", f"scriptlet-{i+1}")))
        return symbols

    def _markdown(self, path, content):
        symbols = []
        for i, line in enumerate(content.splitlines()):
            m = re.match(r"^(#+)\s+(.*)", line.strip())
            if m: symbols.append((path, m.group(2), "doc", i+1, i+1, line.strip(), "", json.dumps({"lvl": len(m.group(1))}), "", m.group(2), _symbol_id(path, "doc", m.group(2))))
        return symbols