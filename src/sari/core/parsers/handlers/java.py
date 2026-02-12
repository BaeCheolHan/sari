import re
from typing import Dict, List, Tuple, Optional
from sari.core.parsers.base import BaseHandler


class JavaHandler(BaseHandler):
    def handle_node(self,
                    node: object,
                    get_t,
                    find_id,
                    ext: str,
                    p_meta: Dict[str,
                                 object]) -> Tuple[Optional[str],
                                                Optional[str],
                                                Dict[str,
                                                     object],
                                                bool]:
        n_type = node.type
        meta = {
            "annotations": [],
            "generated": False,
            "reactive": False,
            "extends": [],
            "framework_role": "",
            "return_type": "",
            "api": False}

        meta["annotations"] = self._extract_annotations_ast(node, get_t)

        if n_type in (
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
                "object_declaration"):
            # Use pure identifier preference for reliable name extraction
            name = find_id(node, prefer_pure_identifier=True)
            txt = get_t(node)

            # Inheritance
            for c in node.children:
                if c.type in (
                    "superclass",
                    "extends_interfaces",
                        "interfaces"):
                    meta["extends"].extend(re.findall(
                        r"[A-Z][a-zA-Z0-9_]*", get_t(c)))

            # Framework Logic
            if any("EntityPathBase" in x for x in meta["extends"]):
                meta["generated"] = True
                meta["framework"] = "QueryDSL"

            if "JpaRepository" in txt or "JpaRepository" in str(
                    meta["extends"]):
                meta["framework_role"] = "Repository"
            if any(a in meta["annotations"]
                   for a in ("RestController", "Controller")):
                meta["framework_role"] = "Controller"
            if "Service" in meta["annotations"]:
                meta["framework_role"] = "Service"
            if "Configuration" in meta["annotations"]:
                meta["framework_role"] = "Config"

            return "class", name, meta, True

        if n_type in ("method_declaration", "constructor_declaration"):
            name = find_id(node, prefer_pure_identifier=True)
            txt = get_t(node)

            for c in node.children:
                if c.type in (
                    "type_identifier",
                    "generic_type",
                    "void_type",
                        "boolean_type"):
                    meta["return_type"] = get_t(c)
                    break

            if any(r in txt for r in ("Mono", "Flux")):
                meta["reactive"] = True

            # API detection is handled by extract_api_info, but flag it here
            # too
            if any(
                a in meta["annotations"] for a in (
                    "GetMapping",
                    "PostMapping",
                    "RequestMapping",
                    "Bean")):
                meta["api"] = True

            return "method", name, meta, True

        return None, None, {}, False

    def _extract_annotations_ast(self, node: object, get_t) -> List[str]:
        annos = []
        modifiers = None
        for c in node.children:
            if c.type == "modifiers":
                modifiers = c
                break

        if modifiers:
            for c in modifiers.children:
                if c.type in ("marker_annotation", "annotation"):
                    for cc in c.children:
                        if cc.type in ("identifier", "scoped_identifier"):
                            annos.append(get_t(cc))
                            break
        return annos

    def extract_api_info(self, node: object, get_t, get_child) -> Dict:
        """Restored from backup: Extracts HTTP method/path from annotations."""
        res = {"http_path": None, "http_methods": []}
        modifiers = get_child(node, "modifiers")
        if not modifiers:
            return res

        for c in modifiers.children:
            if c.type in ("marker_annotation", "annotation"):
                ann_id = get_child(c, "identifier", "scoped_identifier")
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

                        # Extract path argument
                        args = get_child(
                            c, "annotation_argument_list", "arguments")
                        if args:
                            for arg in args.children:
                                if arg.type == "string_literal":
                                    res["http_path"] = get_t(arg).strip("'\"")
                                elif arg.type == "assignment_expression":
                                    left = get_child(arg, "identifier")
                                    if left and get_t(left) in (
                                            "value", "path"):
                                        right = get_child(
                                            arg, "string_literal")
                                        if right:
                                            res["http_path"] = get_t(
                                                right).strip("'\"")
        return res
