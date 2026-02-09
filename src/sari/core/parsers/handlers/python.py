import json
from typing import Any, Dict, Optional, Tuple
from ..base import BaseHandler

class PythonHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {"annotations": []}
        is_valid = False
        
        # Priority Logic: Extraction from backup truth
        if n_type == "class_definition":
            kind, is_valid = "class", True
            name = find_id(node)
        elif n_type == "function_definition":
            kind, is_valid = "function", True
            name = find_id(node)
            # Decorator Extraction (Look at parent decorated_definition)
            p = node.parent
            if p and p.type == "decorated_definition":
                for c in p.children:
                    if c.type == "decorator":
                        meta["annotations"].append(get_t(c).strip("@").split("(")[0])
        
        return kind, name, meta, is_valid

    def extract_api_info(self, node: Any, get_t: callable, get_child: callable) -> Dict:
        res = {"http_path": None, "http_methods": []}
        if node.type == "decorated_definition":
            for c in node.children:
                if c.type == "decorator":
                    txt = get_t(c)
                    if any(r in txt for r in (".get(", ".post(", ".route(")):
                        try:
                            res["http_path"] = txt.split("(")[1].split(")")[0].strip("'\"")
                            if ".get" in txt: res["http_methods"] = ["GET"]
                            elif ".post" in txt: res["http_methods"] = ["POST"]
                        except Exception: 
                            pass # Still pass for minor parsing error but could log debug
        return res
