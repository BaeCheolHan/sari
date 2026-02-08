import re
from typing import Any, Dict, List, Tuple, Optional
from sari.core.parsers.base import BaseHandler

class JavaScriptHandler(BaseHandler):
    def handle_node(self, node: Any, get_t, find_id, ext: str, p_meta: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, Any], bool]:
        n_type = node.type
        name = find_id(node)
        meta = {"vue_option": False, "arrow": False}

        # Vue Options API
        if n_type in ("method_definition", "pair"):
            key_node = node.children[0]
            key_name = get_t(key_node).strip().strip("'\"")
            if key_name in ("data", "methods", "computed", "watch", "created", "mounted", "props"):
                return "method", key_name, {"vue_option": True}, True

        # Express Route (Symbol Detection)
        if n_type == "call_expression":
            txt = get_t(node)
            m = re.search(r"\.(get|post|put|delete|patch|use)\(['\"]([^'\"]+)['\"]", txt)
            if m:
                method = m.group(1).lower()
                path = m.group(2)
                # Ensure unique name for the symbol
                sym_name = f"route.{method}:{path}"
                return "method", sym_name, {"http_method": method, "route_path": path, "framework": "express"}, True

        # React / Class / Function
        is_comp = name and name[0].isupper() and len(name) > 1
        if n_type in ("class_declaration", "function_declaration", "method_definition"):
            kind = "class" if "class" in n_type or is_comp else "function"
            return kind, name, meta, True

        # Arrow Function
        if n_type == "variable_declarator" and "=>" in get_t(node):
            kind = "class" if is_comp else "function"
            return kind, name, {"arrow": True}, True

        return None, None, {}, False

    def extract_api_info(self, node: Any, get_t, get_child) -> Dict:
        """Extract Express route info for metadata enrichment."""
        res = {"http_path": None, "http_methods": []}
        if node.type == "call_expression":
            txt = get_t(node)
            m = re.search(r"\.(get|post|put|delete|patch)\(['\"]([^'\"]+)['\"]", txt)
            if m:
                res["http_methods"].append(m.group(1).upper())
                res["http_path"] = m.group(2)
        return res
