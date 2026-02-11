from typing import Dict, Optional, Tuple
from .java import BaseHandler


class KotlinHandler(BaseHandler):
    def handle_node(self,
                    node: object,
                    get_t: callable,
                    find_id: callable,
                    ext: str,
                    p_meta: Dict) -> Tuple[Optional[str],
                                           Optional[str],
                                           Dict,
                                           bool]:
        n_type = node.type
        kind, name, meta = None, None, {"annotations": []}
        is_valid = False

        if n_type in ("class_declaration", "object_declaration"):
            kind, is_valid = "class", True
            name = find_id(node)
            meta["annotations"] = self._extract_annotations(node, get_t)
            # kotlin specific
            txt = get_t(node)
            if "data class" in txt:
                meta["kotlin_type"] = "data_class"
            if "sealed class" in txt:
                meta["kotlin_type"] = "sealed_class"
            if n_type == "object_declaration":
                meta["kotlin_type"] = "object"

        elif n_type == "function_declaration":
            kind, is_valid = "function", True
            name = find_id(node)
            meta["annotations"] = self._extract_annotations(node, get_t)
            if "suspend" in get_t(node):
                meta["kotlin_coroutine"] = True

        return kind, name, meta, is_valid

    def _extract_annotations(self, node: object, get_t: callable) -> list:
        annotations = []
        # Kotlin annotations are often in 'modifiers' -> 'annotation'
        for c in node.children:
            if c.type == "modifiers":
                for mc in c.children:
                    if mc.type == "annotation":
                        # Find user_type or identifier
                        for ann_c in mc.children:
                            if ann_c.type in ("user_type", "type_identifier"):
                                annotations.append(get_t(ann_c))
                                break
        return annotations

    def extract_api_info(
            self,
            node: object,
            get_t: callable,
            get_child: callable) -> Dict:
        # Kotlin/Spring Boot support
        res = {"http_path": None, "http_methods": []}
        modifiers = get_child(node, "modifiers")
        if not modifiers:
            return res
        for mc in modifiers.children:
            if mc.type == "annotation":
                ann_id = get_child(mc, "user_type", "type_identifier")
                if ann_id:
                    ann_name = get_t(ann_id)
                    if ann_name in (
                        "GetMapping",
                        "PostMapping",
                        "PutMapping",
                        "DeleteMapping",
                        "PatchMapping",
                            "RequestMapping"):
                        if ann_name != "RequestMapping":
                            res["http_methods"].append(
                                ann_name.replace("Mapping", "").upper())
                        # Path extraction (similar to Java)
                        args = get_child(
                            mc, "annotation_argument_list", "arguments", "value_argument")
                        if args:
                            # Kotlin value_argument can be complex
                            txt = get_t(args)
                            import re
                            m = re.search(r'["\']([^"\']+)["\']', txt)
                            if m:
                                res["http_path"] = m.group(1)
        return res
