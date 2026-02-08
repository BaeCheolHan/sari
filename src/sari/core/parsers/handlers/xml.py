import re
from typing import Any, Dict, List, Tuple, Optional
from sari.core.parsers.base import BaseHandler

class XmlHandler(BaseHandler):
    def handle_node(self, node: Any, get_t, find_id, ext: str, p_meta: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, Any], bool]:
        txt = get_t(node)
        
        # MyBatis (Test expects kind='method')
        m = re.search(r'<(select|insert|update|delete|sql)\s+id=["\']([^"\']+)["\']', txt)
        if m:
            return "method", m.group(2), {"mybatis_tag": m.group(1)}, True
            
        # General Tag
        m = re.search(r'<([a-zA-Z0-9:-]+)\s+[^>]*id=["\']([^"\']+)["\']', txt)
        if m:
            return "function", m.group(2), {"tag": m.group(1)}, True

        return None, None, {}, False