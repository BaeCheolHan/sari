from typing import Any, Dict, List


class CallGraphService:
    def __init__(self, db: Any, roots: List[str]):
        self.db = db
        self.roots = roots

    def build(self, args: Dict[str, Any]) -> Dict[str, Any]:
        # Local import to avoid circular dependency at import time.
        from sari.mcp.tools.call_graph import build_call_graph
        return build_call_graph(args, self.db, self.roots)
