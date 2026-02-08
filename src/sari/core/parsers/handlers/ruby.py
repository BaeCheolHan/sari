from typing import Any, Dict, List, Tuple, Optional
from sari.core.parsers.base import BaseHandler

class RubyHandler(BaseHandler):
    def handle_node(self, node: Any, get_t, find_id, ext: str, p_meta: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, Any], bool]:
        n_type = node.type
        if n_type == "class":
            return "class", find_id(node), {}, True
        if n_type == "method":
            return "method", find_id(node), {}, True
        return None, None, {}, False