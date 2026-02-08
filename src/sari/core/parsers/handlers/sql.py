from typing import Any, Dict, Optional, Tuple
from sari.core.parsers.base import BaseHandler

class SQLHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {}
        is_valid = False
        
        if n_type == "create_table_statement":
            kind, is_valid = "class", True
            # Search for relation_name or identifier
            name_node = next((c for c in node.children if c.type in ("relation_name", "identifier")), None)
            name = get_t(name_node) if name_node else find_id(node)
        elif n_type in ("select_statement", "insert_statement", "update_statement", "delete_statement"):
            # Optional: handle queries?
            pass

        return kind, name, meta, is_valid
