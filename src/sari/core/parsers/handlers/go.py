import json
from typing import Any, Dict, Optional, Tuple
from .java import BaseHandler

class GoHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta, is_valid = None, None, {"annotations": []}, False
        
        if n_type in ("type_declaration", "struct_spec", "interface_spec"):
            kind, is_valid = "class", True
            name = find_id(node)
        elif n_type == "function_declaration":
            kind, is_valid = "function", True
            name = find_id(node)
        elif n_type == "method_declaration":
            kind, is_valid = "method", True
            name = find_id(node)
            
        return kind, name, meta, is_valid

    def extract_api_info(self, node: Any, get_t: callable, get_child: callable) -> Dict:
        res = {"http_path": None, "http_methods": []}
        # Go (Gin/Echo): r.GET("/path", handler)
        if node.type == "call_expression":
            fn = get_child(node, "selector_expression", "identifier")
            if fn:
                fn_txt = get_t(fn)
                for m in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                    if f".{m}" in fn_txt or fn_txt == m:
                        res["http_methods"] = [m]
                        args = get_child(node, "argument_list")
                        if args and len(args.children) > 1:
                            # Usually the first argument is the path string
                            path_node = args.children[1] # [0] is '(', [1] is first arg
                            if path_node.type == "interpreted_string_literal":
                                res["http_path"] = get_t(path_node).strip('"`')
                        break
        return res