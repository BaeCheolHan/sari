import json
from typing import Any, Dict, Optional, Tuple
from sari.core.parsers.base import BaseHandler

class RustHandler(BaseHandler):
    def handle_node(self, node: Any, get_t: callable, find_id: callable, ext: str, p_meta: Dict) -> Tuple[Optional[str], Optional[str], Dict, bool]:
        n_type = node.type
        kind, name, meta, is_valid = None, None, {"annotations": []}, False
        
        if n_type in ("struct_item", "enum_item", "trait_item", "impl_item"):
            kind, is_valid = "class", True
            name = find_id(node)
        elif n_type == "function_item":
            kind, is_valid = "function", True
            name = find_id(node)
            
        return kind, name, meta, is_valid

    def extract_api_info(self, node: Any, get_t: callable, get_child: callable) -> Dict:
        res = {"http_path": None, "http_methods": []}
        # Rust (Actix-web): #[get("/")]
        if node.type == "attribute_item":
            txt = get_t(node)
            for m in ("get", "post", "put", "delete", "patch"):
                if f"#[{m}" in txt:
                    res["http_methods"] = [m.upper()]
                    import re
                    match = re.search(r'\(["\']([^"\']+)["\']\)', txt)
                    if match:
                        res["http_path"] = match.group(1)
                    break
        return res