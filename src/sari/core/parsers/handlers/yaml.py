import re
import json
from typing import Any, Dict, Optional, Tuple
from .java import BaseHandler

class YAMLHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta = None, None, {}
        is_valid = False
        
        if n_type == "block_mapping":
            txt = get_t(node)
            txt_lower = txt.lower()
            # Kubernetes Detection
            if "kind:" in txt_lower and ("apiversion:" in txt_lower or "apiVersion:" in txt):
                kind, is_valid = "class", True
                # Extract kind and name using regex as yaml-ts is flat-ish
                m_kind = re.search(r"kind:\s*(\w+)", txt)
                if m_kind:
                    k8s_kind = m_kind.group(1)
                    m_name = re.search(r"name:\s*([^\s\n]+)", txt)
                    name = f"{k8s_kind}.{m_name.group(1)}" if m_name else k8s_kind
                    meta["k8s_kind"], meta["framework"] = k8s_kind, "Kubernetes"

        return kind, name, meta, is_valid
