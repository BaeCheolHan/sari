import json
import sqlite3
from typing import Any, Dict, List
from sari.mcp.tools._util import mcp_response, pack_header, pack_line, pack_encode_id, pack_encode_text, resolve_root_ids, pack_error, ErrorCode

def execute_search_api_endpoints(args: Dict[str, Any], db: Any, roots: List[str]) -> Dict[str, Any]:
    """Search for API endpoints by URL path pattern."""
    path_query = args.get("path", "").strip()
    if not path_query:
        return mcp_response(
            "search_api_endpoints",
            lambda: pack_error("search_api_endpoints", ErrorCode.INVALID_ARGS, "Path query is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "Path query is required"}, "isError": True},
        )

    repo = args.get("repo")

    # Search in symbols where metadata contains the path
    # SQLite JSON support is limited in older versions, so we use LIKE on metadata TEXT
    sql = """
        SELECT s.path, s.name, s.kind, s.line, s.metadata, s.content, f.repo
        FROM symbols s
        JOIN files f ON s.path = f.path
        WHERE s.metadata LIKE ? AND (s.kind = 'method' OR s.kind = 'function' OR s.kind = 'class')
    """
    # Look for partial matches in metadata (looser LIKE, filter in Python)
    params = [f'%{path_query}%']
    root_ids = resolve_root_ids(roots)
    if root_ids:
        root_clause = " OR ".join(["s.path LIKE ?"] * len(root_ids))
        sql += f" AND ({root_clause})"
        params.extend([f"{rid}/%" for rid in root_ids])
    if repo:
        sql += " AND f.repo = ?"
        params.append(repo)

    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
    rows = conn.execute(sql, params).fetchall()

    results = []
    for r in rows:
        try:
            meta = json.loads(r["metadata"])
            http_path = meta.get("http_path", "")
            if path_query in http_path or path_query == http_path:
                results.append({
                    "path": r["path"],
                    "name": r["name"],
                    "kind": r["kind"],
                    "line": r["line"],
                    "repo": r["repo"],
                    "http_path": http_path,
                    "annotations": meta.get("annotations", []),
                    "snippet": r["content"]
                })
        except:
            continue

    def build_pack() -> str:
        lines = [pack_header("search_api_endpoints", {"q": pack_encode_text(path_query)}, returned=len(results))]
        if not repo:
            lines.append(pack_line("m", {"hint": pack_encode_text("repo 또는 root_ids로 스코프를 고정하세요")}))
        for r in results:
            kv = {
                "path": pack_encode_id(r["path"]),
                "name": pack_encode_id(r["name"]),
                "kind": pack_encode_id(r["kind"]),
                "line": str(r["line"]),
                "http_path": pack_encode_text(r["http_path"]),
                "repo": pack_encode_id(r.get("repo", "")),
            }
            lines.append(pack_line("r", kv))
        return "\n".join(lines)

    return mcp_response(
        "search_api_endpoints",
        build_pack,
        lambda: {"query": path_query, "repo": repo or "", "results": results, "count": len(results), "meta": {"hint": "repo 또는 root_ids로 스코프를 고정하세요" if not repo else ""}},
    )
