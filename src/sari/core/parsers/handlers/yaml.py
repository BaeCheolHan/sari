from typing import Any, Dict, List, Tuple, Optional
from sari.core.parsers.base import BaseHandler

class YAMLHandler(BaseHandler):
    def handle_node(self, node: Any, get_t, find_id, ext: str, p_meta: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, Any], bool]:
        # Simple heuristic for YAML keys as symbols
        if node.type == "block_mapping_pair":
            key_node = node.children[0]
            name = get_t(key_node).strip().rstrip(":")
            return "variable", name, {}, True
        return None, None, {}, False