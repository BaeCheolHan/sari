import json
import sqlite3
from typing import Any, Dict, List
from sari.mcp.tools._util import mcp_response, pack_header, pack_line, pack_encode_id, pack_encode_text, resolve_root_ids, pack_error, ErrorCode

def execute_search_api_endpoints(args: Dict[str, Any], db: Any, roots: List[str]) -> Dict[str, Any]:
    """
    URL 경로 패턴을 기반으로 관련 API 엔드포인트(함수, 메서드)를 검색하는 도구입니다.
    코드 내 메타데이터에 기록된 `http_path` 정보를 활용하여 탐색합니다.
    """
    path_query = args.get("path", "").strip()
    if not path_query:
        return mcp_response(
            "search_api_endpoints",
            lambda: pack_error("search_api_endpoints", ErrorCode.INVALID_ARGS, "Path query is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "Path query is required"}, "isError": True},
        )

    repo = args.get("repo")

    # 메타데이터에 경로가 포함된 심볼 검색
    # SQLite JSON 지원 버전의 한계로 인해, 메타데이터 텍스트에 대해 LIKE 검색을 수행한 후 Python에서 필터링합니다.
    sql = """
        SELECT s.path, s.name, s.kind, s.line, s.meta_json AS metadata, s.content, f.repo
        FROM symbols s
        JOIN files f ON s.path = f.path
        WHERE s.meta_json LIKE ? AND (s.kind = 'method' OR s.kind = 'function' OR s.kind = 'class')
    """
    # 메타데이터에서 부분 일치 검색 (느슨한 LIKE 검색)
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
            meta = json.loads(r["metadata"] or "{}")
            http_path = meta.get("http_path", "")
            # 쿼리가 http_path에 포함되는지 확인
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
        except (json.JSONDecodeError, KeyError) as e:
            import logging
            logging.getLogger("sari.mcp.tools").debug(f"Failed to parse metadata for {r['path']}: {e}")
            continue

    def build_pack() -> str:
        """PACK1 형식의 응답을 생성합니다."""
        lines = [pack_header("search_api_endpoints", {"q": pack_encode_text(path_query)}, returned=len(results))]
        if not repo:
            lines.append(pack_line("m", {"hint": pack_encode_text("Narrow scope using repo or root_ids")}))
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
        lambda: {"query": path_query, "repo": repo or "", "results": results, "count": len(results), "meta": {"hint": "Narrow scope using repo or root_ids" if not repo else ""}},
    )
