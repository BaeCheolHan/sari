import json
import re
from typing import Any, Dict, Optional, Tuple
from .java import BaseHandler

class JavaScriptHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {"annotations": []}
        is_valid = False
        
        txt = get_t(node)
        
        # Helper: Extract name using Regex if AST fails (Highly resilient)
        def fallback_name(pattern, text):
            m = re.search(pattern, text)
            return m.group(1) if m else None

        # 1. Classes & Components
        if "class" in n_type:
            kind, is_valid = "class", True
            name = find_id(node) or fallback_name(r"class\s+([a-zA-Z0-9_]+)", txt)
            
        # 2. Functions (Standard & Async)
        elif "function" in n_type and n_type != "arrow_function":
            kind, is_valid = "function", True
            name = find_id(node) or fallback_name(r"function\s+([a-zA-Z0-9_]+)", txt)
            
        # 3. Declarations (React Arrow Components, Hooks, Constants)
        elif n_type in ("lexical_declaration", "variable_declaration", "variable_declarator"):
            v_name = find_id(node) or fallback_name(r"(?:const|let|var)\s+([a-zA-Z0-9_]+)", txt)
            if v_name:
                name, is_valid = v_name, True
                # Heuristic: Uppercase first letter + React keywords = class (Component)
                if v_name[0].isupper() and any(x in txt for x in ("=>", "React.", "use", "return (", "return <")):
                    kind = "class"
                elif "=>" in txt or "function" in txt:
                    kind = "function"
                else:
                    kind = "variable"
        
        # 4. Methods & Fields
        elif "method" in n_type or n_type == "public_field_definition":
            kind, is_valid = "method", True
            name = find_id(node) or fallback_name(r"([a-zA-Z0-9_]+)\s*\(", txt)

        # 5. Express Routes (Special case for search_api_endpoints)
        elif n_type == "call_expression":
            if any(m in txt for m in (".get(", ".post(", ".put(", ".delete(")):
                for m in ("GET", "POST", "PUT", "DELETE"):
                    if f".{m.lower()}(" in txt:
                        kind, is_valid, name = "method", True, f"route.{m}"
                        break

        return kind, name, meta, is_valid

    def extract_api_info(self, node: Any, get_t: callable, get_child: callable) -> Dict:
        res = {"http_path": None, "http_methods": []}
        if node.type == "call_expression":
            txt = get_t(node)
            for m in ("get", "post", "put", "delete", "patch", "use"):
                if f".{m}(" in txt:
                    res["http_methods"] = [m.upper()]
                    # Look for the first string argument as the path
                    import re
                    path_match = re.search(r'["\']([^"\']+)["\']', txt)
                    if path_match:
                        res["http_path"] = path_match.group(1)
                    break
        return res