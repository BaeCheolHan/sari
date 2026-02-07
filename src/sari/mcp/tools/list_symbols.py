from typing import Dict, Any, List, Optional
from sari.core.db.main import LocalSearchDB
from sari.mcp.tools._util import (
    ErrorCode,
    mcp_response,
    pack_error,
    pack_header,
    pack_line,
    resolve_db_path,
    pack_encode_id,
)

def execute_list_symbols(args: Dict[str, Any], db: LocalSearchDB, roots: List[str]) -> Dict[str, Any]:
    """
    List all symbols in a specific file in a hierarchical way.
    Helps LLMs understand file structure without reading the whole body.
    """
    path = args.get("path")
    if not path:
        return mcp_response(
            "list_symbols",
            lambda: pack_error("list_symbols", ErrorCode.INVALID_ARGS, "'path' is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "'path' is required"}, "isError": True},
        )

    db_path = resolve_db_path(path, roots)
    if not db_path:
        return mcp_response(
            "list_symbols",
            lambda: pack_error("list_symbols", ErrorCode.ERR_ROOT_OUT_OF_SCOPE, f"Path out of scope: {path}"),
            lambda: {"error": {"code": ErrorCode.ERR_ROOT_OUT_OF_SCOPE.value, "message": f"Path out of scope: {path}"}, "isError": True},
        )

    # Fetch all symbols for this file
    # We use raw SQL for performance and reliability
    cursor = db.db.execute_sql(
        "SELECT name, kind, line, end_line, parent_name, qualname FROM symbols WHERE path = ? ORDER BY line ASC",
        (db_path,)
    )
    rows = cursor.fetchall()
    
    symbols = []
    for r in rows:
        symbols.append({
            "name": r[0],
            "kind": r[1],
            "line": r[2],
            "end_line": r[3],
            "parent": r[4],
            "qual": r[5]
        })

    def build_tree(nodes, parent=""):
        tree = []
        for n in nodes:
            if n["parent"] == parent:
                children = build_tree(nodes, n["name"])
                item = {"name": n["name"], "kind": n["kind"], "line": n["line"], "end": n["end_line"]}
                if children:
                    item["children"] = children
                tree.append(item)
        return tree

    hierarchical_tree = build_tree(symbols)

    def build_pack() -> str:
        lines = [pack_header("list_symbols", {"path": pack_encode_id(db_path)}, returned=len(symbols))]
        
        # Abbreviated kind mapping for token efficiency
        K = {"class": "C", "function": "F", "method": "M", "variable": "V", "resource": "R", "block": "B"}

        def _flatten_pack(nodes, depth=0):
            for n in nodes:
                indent = "." * depth # Use dots or minimal spaces
                kind_short = K.get(n['kind'], n['kind'][:1].upper())
                lines.append(f"s:{indent}{kind_short}|{pack_encode_id(n['name'])}:{n['line']}")
                if "children" in n:
                    _flatten_pack(n["children"], depth + 1)
        
        _flatten_pack(hierarchical_tree)
        return "\n".join(lines)

    return mcp_response(
        "list_symbols",
        build_pack,
        lambda: {"path": db_path, "symbols": hierarchical_tree}
    )