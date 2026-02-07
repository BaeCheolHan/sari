import json
from typing import Any, Dict, Optional, Tuple

class BaseHandler:
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        return None, None, {}, False

class JavaHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {"annotations": [], "generated": False, "reactive": False, "return_type": ""}
        is_valid = False
        
        if n_type in ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"):
            kind, is_valid = "class", True
            name = find_id(node)
            if not name:
                for c in node.children:
                    if c.type == "identifier": name = get_t(c); break
            
            meta["annotations"] = self._extract_annotations(node, get_t)
            if "JpaRepository" in get_t(node):
                meta["framework_role"] = "Repository"
            if n_type == "record_declaration": meta["java_type"] = "record"
            
        elif n_type == "method_declaration":
            kind, is_valid = "method", True
            # Find identifier more reliably for Java methods
            for c in node.children:
                if c.type == "identifier":
                    name = get_t(c)
                    break
            if not name: name = find_id(node, prefer_pure_identifier=True)
            
            meta["annotations"] = self._extract_annotations(node, get_t)
            if any(r in get_t(node) for r in ("Mono", "Flux")):
                meta["reactive"], meta["return_type"] = True, "Mono/Flux"

        return kind, name, meta, is_valid

    def _extract_annotations(self, node: Any, get_t: callable) -> list:
        annotations = []
        modifiers = next((c for c in node.children if c.type == "modifiers"), None)
        if modifiers:
            for c in modifiers.children:
                if c.type in ("marker_annotation", "annotation"):
                    for cc in c.children:
                        if cc.type in ("identifier", "scoped_identifier"):
                            annotations.append(get_t(cc))
                            break
        return annotations

    def extract_api_info(self, node: Any, get_t: callable, get_child: callable) -> Dict:
        res = {"http_path": None, "http_methods": []}
        modifiers = get_child(node, "modifiers")
        if not modifiers: return res

        for c in modifiers.children:
            if c.type in ("marker_annotation", "annotation"):
                ann_id = get_child(c, "identifier", "scoped_identifier")
                if ann_id:
                    ann_name = get_t(ann_id)
                    if ann_name in ("GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping"):
                        if ann_name != "RequestMapping":
                            res["http_methods"].append(ann_name.replace("Mapping", "").upper())
                        
                        args = get_child(c, "annotation_argument_list", "arguments")
                        if args:
                            for arg in args.children:
                                if arg.type == "string_literal":
                                    res["http_path"] = get_t(arg).strip("'\"")
                                elif arg.type == "assignment_expression":
                                    left = get_child(arg, "identifier")
                                    if left and get_t(left) in ("value", "path"):
                                        right = get_child(arg, "string_literal")
                                        if right: res["http_path"] = get_t(right).strip("'\"")
        return res