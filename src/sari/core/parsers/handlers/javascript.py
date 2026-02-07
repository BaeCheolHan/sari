import json
from typing import Any, Dict, Optional, Tuple
from .java import BaseHandler

class JavaScriptHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {"annotations": []}
        is_valid = False
        
        # Priority Logic: Pure Truth Restoration
        if n_type in ("class_declaration", "class"):
            kind, is_valid = "class", True
            name = find_id(node)
            
        elif n_type in ("function_declaration", "function"):
            kind, is_valid = "function", True
            name = find_id(node)
            
        elif n_type in ("lexical_declaration", "variable_declaration", "variable_declarator"):
            # Deep dive for arrow functions / components
            v_name = find_id(node)
            if v_name:
                name, is_valid = v_name, True
                txt = get_t(node)
                if v_name[0].isupper() and any(x in txt for x in ("=>", "React.", "Component", "memo", "forwardRef")):
                    kind = "class"
                elif "=>" in txt or "function" in txt:
                    kind = "function"
                else:
                    kind = "variable"
        
        elif "method" in n_type:
            kind, is_valid = "method", True
            name = find_id(node)

        # Express route symbols (compatibility for tests)
        elif n_type == "call_expression":
            txt = get_t(node)
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
                    import re
                    match = re.search(r'["\']([^"\']+)["\']', txt)
                    if match: res["http_path"] = match.group(1)
                    break
        return res
