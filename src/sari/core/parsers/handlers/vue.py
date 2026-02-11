import re
from typing import Dict, Optional, Tuple
from sari.core.parsers.base import BaseHandler

class VueHandler(BaseHandler):
    def handle_node(self, node: object, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        kind, name, meta = None, None, {}
        is_valid = False
        
        txt = get_t(node)
        
        # 1. Component Name (Fallback to Regex for safety)
        m_name = re.search(r"name:\s*['\"]([^'\"]+)['\"]", txt)
        if m_name:
            kind, is_valid, name = "class", True, m_name.group(1)
        
        # 2. Options API / Methods / Data / Computed (Regex Fallback)
        if not is_valid:
            # Extract common properties like data, created, methods
            m_prop = re.search(r"^\s*([a-zA-Z0-9_]+)\s*[:\(]", txt, re.MULTILINE)
            if m_prop:
                v_name = m_prop.group(1)
                if v_name in ("data", "created", "mounted", "updated", "destroyed", "methods", "computed", "watch"):
                    kind, is_valid, name = "method", True, v_name
            
            # Extract methods inside methods/computed block using indentation/pattern
            if not is_valid:
                m_method = re.search(r"^ {2,4}([a-zA-Z0-9_]+)\s*\(", txt, re.MULTILINE)
                if m_method:
                    kind, is_valid, name = "method", True, m_method.group(1)

        return kind, name, meta, is_valid
