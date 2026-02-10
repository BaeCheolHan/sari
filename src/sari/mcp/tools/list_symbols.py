from typing import Dict, Any, List, Optional
from sari.core.db.main import LocalSearchDB
from sari.mcp.tools._util import (
    ErrorCode,
    mcp_response,
    pack_error,
    pack_header,
    pack_line,
    resolve_db_path,
    handle_db_path_error,
    pack_encode_id,
)

def execute_list_symbols(args: Dict[str, Any], db: LocalSearchDB, roots: List[str]) -> Dict[str, Any]:
    """
    특정 파일 내의 모든 심볼을 계층적 구조로 나열합니다.
    LLM이 파일 전체를 읽지 않고도 파일의 구조(클래스, 함수 등)를 파악하는 데 도움을 줍니다.
    """
    path = args.get("path")
    if not path:
        return mcp_response(
            "list_symbols",
            lambda: pack_error("list_symbols", ErrorCode.INVALID_ARGS, "'path' is required"),
            lambda: {"error": {"code": ErrorCode.INVALID_ARGS.value, "message": "'path' is required"}, "isError": True},
        )

    db_path = resolve_db_path(path, roots, db=db)
    if not db_path:
        return handle_db_path_error("list_symbols", path, roots, db)

    # 해당 파일의 모든 심볼 조회
    # 성능과 신뢰성을 위해 ORM 대신 Raw SQL 사용
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

    def build_tree(nodes, parent="", depth=0):
        if depth > 10:  # 무한 재귀 방지용 안전 장치
            return []
        tree = []
        for n in nodes:
            # 부모 이름이 일치하거나, 최상위 노드(parent가 빈 문자열)인 경우 처리
            # 실제로는 parent_name 필드가 정확하지 않을 수 있어 개선 필요할 수 있음
            if n["parent"] == parent or (parent == "" and not n["parent"]):
                # 자식 노드 재귀 탐색 (현재 구조상 parent 로직이 단순하여 모든 계층을 완벽히 표현하지 못할 수 있음)
                # 여기서는 간단히 이름 기반으로 매칭 시도
                children = build_tree(nodes, n["name"], depth + 1)
                item = {"name": n["name"], "kind": n["kind"], "line": n["line"], "end": n["end_line"]}
                if children:
                    item["children"] = children
                tree.append(item)
        return tree

    # 계층 구조 생성 (다만 현재 parent_name 로직상 평면적으로 나올 수 있음)
    hierarchical_tree = build_tree(symbols)
    
    # 만약 트리가 비어있고 심볼은 있다면 평면적으로라도 반환 (Fallback)
    if not hierarchical_tree and symbols:
        hierarchical_tree = [{"name": s["name"], "kind": s["kind"], "line": s["line"], "end": s["end_line"]} for s in symbols]

    def build_pack() -> str:
        lines = [pack_header("list_symbols", {"path": pack_encode_id(db_path)}, returned=len(symbols))]
        
        # 토큰 효율성을 위한 심볼 종류 약어 매핑
        K = {"class": "C", "function": "F", "method": "M", "variable": "V", "resource": "R", "block": "B"}

        def _flatten_pack(nodes, depth=0):
            for n in nodes:
                indent = "." * depth # 들여쓰기를 점으로 표현하여 구조 시각화
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