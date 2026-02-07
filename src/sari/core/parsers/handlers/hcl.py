from typing import Any, Dict, Optional, Tuple
from .java import BaseHandler

class HCLHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {}
        is_valid = False
        
        if n_type == "block" and ext in ("hcl", "tf"):
            kind, is_valid = "class", True
            labels = []
            for c in node.children:
                if c.type == "identifier":
                    labels.append(get_t(c))
                elif c.type == "string_lit":
                    # find template_literal or similar
                    tokens = get_t(c).strip('"')
                    labels.append(tokens)
            name = ".".join(labels) if labels else "block"

        return kind, name, meta, is_valid
