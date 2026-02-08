import json
import os
import importlib
from pathlib import Path
from typing import Any, Dict, List

from ._util import mcp_response, pack_header, pack_line, pack_encode_text, pack_encode_id
try:
    from .call_graph import PLUGIN_API_VERSION
except ImportError:
    PLUGIN_API_VERSION = 1

def _load_plugins() -> List[str]:
    mod_path = os.environ.get("SARI_CALLGRAPH_PLUGIN", "").strip()
    if not mod_path:
        return []
    return [m.strip() for m in mod_path.split(",") if m.strip()]

def execute_call_graph_health(args: Dict[str, Any], db: Any, logger: Any = None, roots: List[str] = None) -> Dict[str, Any]:
    def build_pack(payload: Dict[str, Any]) -> str:
        header = pack_header("call_graph_health", {}, returned=1)
        lines = [header]
        for p in payload.get("plugins", []):
            lines.append(pack_line("p", {
                "name": pack_encode_id(p["name"]),
                "status": pack_encode_id(p["status"]),
                "version": str(p.get("version", 0))
            }))
        return "\n".join(lines)

    plugins = _load_plugins()
    results = []
    for p in plugins:
        try:
            mod = importlib.import_module(p)
            results.append({"name": p, "status": "loaded", "version": getattr(mod, "VERSION", PLUGIN_API_VERSION)})
        except Exception as e:
            results.append({"name": p, "status": f"error: {str(e)}", "version": 0})

    payload = {"plugins": results, "api_version": PLUGIN_API_VERSION}
    return mcp_response("call_graph_health", lambda: build_pack(payload), lambda: payload)