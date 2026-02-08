import json
from typing import Any, Dict, Optional, Tuple
from sari.core.parsers.base import BaseHandler

class BashHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {}
        is_valid = False
        
        if n_type == "function_definition":
            kind, is_valid = "method", True
            # In bash tree-sitter, the name is usually under a 'word' node
            for c in node.children:
                if c.type == "word":
                    name = get_t(c)
                    break
        elif n_type == "variable_assignment":
            # Variable detection (optional, but requested in some tests)
            for c in node.children:
                if c.type == "variable_name":
                    kind, is_valid = "variable", True
                    name = get_t(c)
                    break
                    
        return kind, name, meta, is_valid
