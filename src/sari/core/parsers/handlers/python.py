import json
from typing import Any, Dict, Optional, Tuple
from .java import BaseHandler

class PythonHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta, is_valid = None, None, {"annotations": []}, False
        
        if n_type == "class_definition":
            kind, is_valid = "class", True
            name = find_id(node)
        elif n_type == "function_definition":
            kind, is_valid = "function", True
            name = find_id(node)
        elif n_type == "decorated_definition":
            # Support for FastAPI/Flask style decorated functions
            func_def = None
            for c in node.children:
                if c.type == "function_definition":
                    func_def = c
                    break
            if func_def:
                kind, is_valid = "function", True
                name = find_id(func_def)
                meta["annotations"] = self._extract_annotations(node, get_t)
            
        return kind, name, meta, (kind is not None)

    def _extract_annotations(self, node: Any, get_t: callable) -> list:
        annotations = []
        def collect(n):
            if n.type == "decorator":
                for c in n.children:
                    if c.type in ("identifier", "call"):
                        t = get_t(c).split("(")[0].strip("@")
                        annotations.append(t)
                        # Add leaf name e.g. "route" from "app.route"
                        if "." in t:
                            annotations.append(t.split(".")[-1])
            for c in n.children:
                if c.type == "decorator_list": collect(c)
                elif c.type == "decorator": collect(c)
        collect(node)
        return annotations

    def extract_api_info(self, node: Any, get_t: callable, get_child: callable) -> Dict:
        res = {"http_path": None, "http_methods": []}
        if node.type == "decorated_definition":
            decorator = get_child(node, "decorator")
            func_def = get_child(node, "function_definition")
            if decorator and func_def:
                dec_text = get_t(decorator)
                if any(route in dec_text for route in ("@app.", "@router.", "@blueprint.")):
                    call = get_child(decorator, "call")
                    if call:
                        args = get_child(call, "argument_list")
                        if args:
                            for arg in args.children:
                                if arg.type == "string":
                                    res["http_path"] = get_t(arg).strip("'\"")
                                    break
                    if ".get(" in dec_text: res["http_methods"] = ["GET"]
                    elif ".post(" in dec_text: res["http_methods"] = ["POST"]
        return res