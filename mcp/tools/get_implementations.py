import json
from typing import Any, Dict, List
try:
    from ._util import mcp_json
except ImportError:
    from _util import mcp_json

def execute_get_implementations(args: Dict[str, Any], db: Any) -> Dict[str, Any]:
    """Find symbols that implement or extend a specific symbol."""
    target_symbol = args.get("name", "").strip()
    if not target_symbol:
        return mcp_json({"results": [], "error": "Symbol name is required"})

    # Search in symbol_relations table for implements and extends relations
    sql = """
        SELECT from_path, from_symbol, rel_type, line
        FROM symbol_relations
        WHERE to_symbol = ? AND (rel_type = 'implements' OR rel_type = 'extends')
        ORDER BY from_path, line
    """
    params = [target_symbol]
    
    with db._read_lock:
        rows = db._read.execute(sql, params).fetchall()

    results = []
    for r in rows:
        results.append({
            "implementer_path": r["from_path"],
            "implementer_symbol": r["from_symbol"],
            "rel_type": r["rel_type"],
            "line": r["line"]
        })

    return mcp_json({
        "target": target_symbol,
        "results": results,
        "count": len(results)
    })
