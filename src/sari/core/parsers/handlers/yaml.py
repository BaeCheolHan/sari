from typing import Any, Dict, List, Tuple, Optional
from sari.core.parsers.base import BaseHandler

class YAMLHandler(BaseHandler):
    def handle_node(self, node: Any, get_t, find_id, ext: str, p_meta: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, Any], bool]:
        # YAML keys as symbols
        if node.type == "block_mapping_pair":
            key_node = node.children[0]
            key = get_t(key_node).strip().rstrip(":")
            
            # K8s Special Handling: Extract 'kind' value
            if key == "kind":
                # YAML structure: key : value (3 children: key, colon, value)
                val_node = node.children[2] if len(node.children) > 2 else (node.children[1] if len(node.children) > 1 else None)
                if val_node:
                    val = get_t(val_node).strip()
                    return "class", val, {}, True
            
            # K8s Special Handling: Extract metadata.name
            if key == "name":
                # Check if parent is metadata
                p = node.parent
                try:
                    grandparent = p.parent.parent
                    if grandparent.type == "block_mapping_pair":
                        gp_key = get_t(grandparent.children[0]).strip().rstrip(":")
                        if gp_key == "metadata":
                            val_node = node.children[2] if len(node.children) > 2 else (node.children[1] if len(node.children) > 1 else None)
                            if val_node:
                                val = get_t(val_node).strip()
                                return "variable", val, {}, True
                except: pass

            return "variable", key, {}, True
            
        return None, None, {}, False