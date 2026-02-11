from typing import Dict, Tuple, Optional
from sari.core.parsers.base import BaseHandler


class RubyHandler(BaseHandler):
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
        # if node.type == "class": print(f"DEBUG RUBY CLASS: children={[c.type for c in node.children]}")
        n_type = node.type
        if n_type in ("class", "module"):
            # Try finding constant child first
            name = None
            for c in node.children:
                if c.type == "constant":
                    name = get_t(c)
                    break
            if not name:
                name = find_id(node)
            return n_type, name, {}, True
        if n_type == "method":
            return "method", find_id(node), {}, True
        return None, None, {}, False
