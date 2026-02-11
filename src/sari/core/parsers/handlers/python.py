from typing import Dict, Optional, Tuple, List
from ..base import BaseHandler
from sari.core.models import ParserRelation


class PythonHandler(BaseHandler):
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
                        meta["annotations"].append(
                            get_t(c).strip("@").split("(")[0])

        return kind, name, meta, is_valid

    def extract_api_info(
            self,
            node: object,
            get_t: callable,
            get_child: callable) -> Dict:
        res = {"http_path": None, "http_methods": []}
        if node.type == "decorated_definition":
            for c in node.children:
                if c.type == "decorator":
                    txt = get_t(c)
                    if any(r in txt for r in (".get(", ".post(", ".route(")):
                        try:
                            res["http_path"] = txt.split("(")[1].split(")")[
                                0].strip("'\"")
                            if ".get" in txt:
                                res["http_methods"] = ["GET"]
                            elif ".post" in txt:
                                res["http_methods"] = ["POST"]
                        except Exception:
                            pass  # Still pass for minor parsing error but could log debug
        return res

    def handle_relation(
            self,
            node: object,
            context: Dict) -> List[ParserRelation]:
        relations = []
        n_type = node.type
        get_t = context.get("get_t")
        line = node.start_point[0] + 1

        # from_ info will be filled by ASTEngine's stack management
        f_name = context.get("parent_name", "")
        f_sid = context.get("parent_sid", "")

        if n_type == "call":
            # Function/Method call
            fn_node = node.children[0]
            to_name = None
            if fn_node.type == "identifier":
                to_name = get_t(fn_node)
            elif fn_node.type == "attribute":
                # Handle obj.method()
                for c in fn_node.children:
                    # This is the method name (last identifier)
                    if c.type == "identifier":
                        to_name = get_t(c)

            if to_name:
                relations.append(ParserRelation(
                    from_name=f_name, from_sid=f_sid,
                    to_name=to_name, rel_type="calls", line=line
                ))

        elif n_type == "class_definition":
            # Inheritance
            arg_list = None
            for c in node.children:
                if c.type == "argument_list":
                    arg_list = c
                    break

            if arg_list:
                for c in arg_list.children:
                    to_name = None
                    if c.type == "identifier":
                        to_name = get_t(c)
                    elif c.type == "attribute":
                        # Handle class A(module.B)
                        for attr_c in c.children:
                            if attr_c.type == "identifier":
                                to_name = get_t(attr_c)

                    if to_name:
                        relations.append(ParserRelation(
                            from_name=f_name, from_sid=f_sid,
                            to_name=to_name, rel_type="extends", line=line
                        ))

        return relations
