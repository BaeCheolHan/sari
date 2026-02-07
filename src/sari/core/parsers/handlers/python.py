import json
from typing import Any, Dict, Optional, Tuple
from .java import BaseHandler

class PythonHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {"annotations": []}
        is_valid = False
        
        # Priority Fix: Direct identifier extraction for Python
        def get_py_id(n):
            for c in n.children:
                if c.type == "identifier": return get_t(c)
            return None

        if n_type == "class_definition":
            kind, is_valid = "class", True
            name = get_py_id(node)
        elif n_type == "function_definition":
            kind, is_valid = "function", True
            name = get_py_id(node)
            # Extract decorators
            parent = node.parent
            if parent and parent.type == "decorated_definition":
                for c in parent.children:
                    if c.type == "decorator":
                        dec_txt = get_t(c).strip("@").split("(")[0]
                        meta["annotations"].append(dec_txt)
            
        return kind, name, meta, is_valid

    def extract_api_info(self, node: Any, get_t: callable, get_child: callable) -> Dict:
        res = {"http_path": None, "http_methods": []}
        if node.type == "decorated_definition":
            for c in node.children:
                if c.type == "decorator":
                    txt = get_t(c)
                    if any(r in txt for r in (".get(", ".post(", ".route(")):
                        res["http_path"] = txt.split("(")[1].split(")")[0].strip("'\"")
                        if ".get" in txt: res["http_methods"] = ["GET"]
                        elif ".post" in txt: res["http_methods"] = ["POST"]
        return res
