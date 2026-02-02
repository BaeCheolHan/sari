import json

def mcp_json(obj):
    """Utility to format dictionary as standard MCP response."""
    res = {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=2)}]}
    if isinstance(obj, dict):
        res.update(obj)
    return res
