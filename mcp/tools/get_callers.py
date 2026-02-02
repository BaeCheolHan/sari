import json
from typing import Any, Dict, List
try:
    from ._util import mcp_json
except ImportError:
    from _util import mcp_json

def execute_get_callers(args: Dict[str, Any], db: Any) -> Dict[str, Any]:
    """Find symbols that call a specific symbol."""
    target_symbol = args.get("name", "").strip()
    if not target_symbol:
        return mcp_json({"results": [], "error": "Symbol name is required"})

    # Search in symbol_relations table
    sql = """
        SELECT from_path, from_symbol, line, rel_type
        FROM symbol_relations
        WHERE to_symbol = ?
        ORDER BY from_path, line
    """
    params = [target_symbol]
    
    with db._read_lock:
        rows = db._read.execute(sql, params).fetchall()

    results = []
    for r in rows:
        results.append({
            "caller_path": r["from_path"],
            "caller_symbol": r["from_symbol"],
            "line": r["line"],
            "rel_type": r["rel_type"]
        })

    return mcp_json({
        "target": target_symbol,
        "results": results,
        "count": len(results)
    })
