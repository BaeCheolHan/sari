from collections.abc import Mapping
from bisect import bisect_left
from pathlib import Path
from typing import TypeAlias

from sari.core.db.main import LocalSearchDB
from sari.mcp.tools._util import (
    ErrorCode,
    mcp_response,
    pack_error,
    pack_header,
    resolve_db_path,
    handle_db_path_error,
    pack_encode_id,
    invalid_args_response,
    require_repo_arg,
)
from sari.mcp.tools._symbol_hydration import hydrate_file_symbols

ToolResult: TypeAlias = dict[str, object]


def _classify_empty_symbols_reason(path: object, db_path: str, conn: object) -> tuple[str, str] | None:
    row = conn.execute(
        "SELECT parse_status, parse_error, ast_status, ast_reason FROM files WHERE path = ? OR rel_path = ? LIMIT 1",
        (db_path, db_path),
    ).fetchone()
    if not row:
        return ("NOT_INDEXED", f"File not found or not indexed: {db_path}")

    parse_status = str(row["parse_status"] or "").strip().lower()
    parse_error = str(row["parse_error"] or "").strip().lower()
    ext = Path(str(path or db_path)).suffix.lower()

    unsupported_reasons = {
        "unsupported",
        "unsupported_extension",
        "unsupported_language",
        "unsupported_filetype",
        "no_parser",
        "no_language",
    }
    parse_failure_reasons = {"parse_error", "syntax_error", "ast_parse_error", "failed"}

    if (
        parse_status in {"failed", "error"}
        or parse_error in parse_failure_reasons
    ):
        return ("PARSE_FAILED", f"Symbol extraction failed for {db_path} (parse failure)")

    lsp_lang_exts = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go",
        ".rs", ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".hpp", ".swift",
    }
    if (
        parse_error in unsupported_reasons
        or (parse_status == "skipped" and parse_error in unsupported_reasons)
        or (ext and ext not in lsp_lang_exts)
    ):
        return ("UNSUPPORTED_LANGUAGE", f"Symbol extraction is not supported for this language: {ext or 'unknown'}")

    return None


def execute_list_symbols(args: object, db: LocalSearchDB, roots: list[str]) -> ToolResult:
    """
    특정 파일 내의 모든 심볼을 계층적 구조로 나열합니다.
    LLM이 파일 전체를 읽지 않고도 파일의 구조(클래스, 함수 등)를 파악하는 데 도움을 줍니다.
    """
    if not isinstance(args, Mapping):
        return invalid_args_response("list_symbols", "args must be an object")
    repo_err = require_repo_arg(args, "list_symbols")
    if repo_err:
        return repo_err

    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
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
    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else db._read
    try:
        rows = conn.execute(
            "SELECT name, kind, line, end_line, parent, qualname FROM symbols WHERE path = ? ORDER BY line ASC",
            (db_path,),
        ).fetchall()
    except (AttributeError, TypeError, ValueError) as exc:
        msg = f"failed to query symbols: {exc}"
        return mcp_response(
            "list_symbols",
            lambda: pack_error("list_symbols", ErrorCode.DB_ERROR, msg),
            lambda: {"error": {"code": ErrorCode.DB_ERROR.value, "message": msg}, "isError": True},
        )
    
    symbols = []
    for row in rows:
        symbols.append({
            "name": row["name"],
            "kind": row["kind"],
            "line": row["line"],
            "end_line": row["end_line"],
            "parent": row["parent"],
            "qual": row["qualname"],
        })

    if not symbols:
        repo = str(args.get("repo", "")).strip()
        _, inserted = hydrate_file_symbols(db=db, roots=roots, repo=repo, path=str(path))
        if inserted > 0:
            rows = conn.execute(
                "SELECT name, kind, line, end_line, parent, qualname FROM symbols WHERE path = ? ORDER BY line ASC",
                (db_path,),
            ).fetchall()
            symbols = [
                {
                    "name": row["name"],
                    "kind": row["kind"],
                    "line": row["line"],
                    "end_line": row["end_line"],
                    "parent": row["parent"],
                    "qual": row["qualname"],
                }
                for row in rows
            ]

    if not symbols:
        classified = _classify_empty_symbols_reason(path, db_path, conn)
        if classified:
            code, message = classified
            return mcp_response(
                "list_symbols",
                lambda: pack_error("list_symbols", code, message),
                lambda: {"error": {"code": code, "message": message}, "isError": True},
            )

    def _node_id(n: dict[str, object]) -> str:
        qual = str(n.get("qual") or "").strip()
        if qual:
            return qual
        return f"{n.get('name','')}@{n.get('line',0)}"

    # parent-name fallback 인덱스(동명 parent disambiguation용)
    symbols_sorted = sorted(symbols, key=lambda n: int(n.get("line") or 0))
    name_to_line_nodes: dict[str, list[tuple[int, str]]] = {}
    for n in symbols_sorted:
        nname = str(n.get("name") or "")
        nid = _node_id(n)
        nline = int(n.get("line") or 0)
        name_to_line_nodes.setdefault(nname, []).append((nline, nid))

    id_to_item: dict[str, dict[str, object]] = {}
    child_ids: dict[str, list[str]] = {}
    roots: list[str] = []

    for n in symbols_sorted:
        nid = _node_id(n)
        id_to_item[nid] = {
            "name": n["name"],
            "kind": n["kind"],
            "line": n["line"],
            "end": n["end_line"],
        }

        parent_id: str | None = None
        qual = str(n.get("qual") or "").strip()
        if qual and "." in qual:
            parent_candidate = qual.rsplit(".", 1)[0]
            if parent_candidate in id_to_item:
                parent_id = parent_candidate
        if parent_id is None:
            parent_name = str(n.get("parent") or "").strip()
            if parent_name:
                candidates = name_to_line_nodes.get(parent_name, [])
                if candidates:
                    lines_only = [ln for ln, _ in candidates]
                    idx = bisect_left(lines_only, int(n.get("line") or 0)) - 1
                    if idx >= 0:
                        parent_id = candidates[idx][1]

        if parent_id:
            child_ids.setdefault(parent_id, []).append(nid)
        else:
            roots.append(nid)

    def _build(nid: str, depth: int = 0) -> dict[str, object]:
        if depth > 20:
            return dict(id_to_item[nid])
        node = dict(id_to_item[nid])
        children = child_ids.get(nid, [])
        if children:
            node["children"] = [_build(cid, depth + 1) for cid in children]
        return node

    hierarchical_tree = [_build(rid) for rid in roots]
    
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
