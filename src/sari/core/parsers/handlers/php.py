from typing import Any, Dict, Optional, Tuple
from .java import BaseHandler

class PHPHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {}
        is_valid = False
        
        if n_type == "class_declaration":
            kind, is_valid = "class", True
            # PHP 'name' node
            for c in node.children:
                if c.type == "name":
                    name = get_t(c)
                    break
        elif n_type == "method_declaration":
            kind, is_valid = "method", True
            for c in node.children:
                if c.type == "name":
                    name = get_t(c)
                    break

        return kind, name, meta, is_valid
