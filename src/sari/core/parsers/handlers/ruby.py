import re
from typing import Any, Dict, Optional, Tuple
from .java import BaseHandler

class RubyHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {}
        is_valid = False
        
        if n_type == "class":
            kind, is_valid = "class", True
            # Ruby constant node for class name
            for c in node.children:
                if c.type == "constant":
                    name = get_t(c)
                    break
            # Find superclass
            for c in node.children:
                if c.type == "superclass":
                    for sc in c.children:
                        if sc.type == "constant":
                            meta["extends"] = [get_t(sc)]
                            if get_t(sc) in ("ApplicationRecord", "ActiveRecord::Base"):
                                meta["framework"], meta["rails_type"] = "Rails", "model"
                            elif get_t(sc) in ("ApplicationController", "ActionController::Base"):
                                meta["framework"], meta["rails_type"] = "Rails", "controller"
                            break
        elif n_type == "method":
            kind, is_valid = "method", True
            name = find_id(node)

        return kind, name, meta, is_valid
