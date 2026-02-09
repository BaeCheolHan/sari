import json
import os
import urllib.parse
import logging
from enum import Enum
from typing import Any, Dict, Optional, List, Callable, Tuple
from pathlib import Path
from sari.core.workspace import WorkspaceManager

logger = logging.getLogger("sari.mcp.tools")
from .protocol import (
    ErrorCode,
    pack_encode_text,
    pack_encode_id,
    pack_header,
    pack_line,
    pack_error,
    pack_truncated
)

def _default_error_hints(tool: str, code: Any, msg: str) -> List[str]:
    """공통적인 실패 사례에 대해 기본 힌트나 후속 조치를 생성합니다."""
    hints: List[str] = []
    code_val = code.value if isinstance(code, Enum) else str(code)
    msg_lower = str(msg or "").lower()

    if "database" in msg_lower or "db" in msg_lower or code_val == ErrorCode.DB_ERROR.value:
        hints.extend([
            "run doctor to diagnose DB/engine status",
            "check db_path setting and rescan",
        ])

    if code_val in {ErrorCode.NOT_INDEXED.value, ErrorCode.ERR_ENGINE_QUERY.value, ErrorCode.ERR_ENGINE_UNAVAILABLE.value}:
        hints.append("run scan_once or rescan to refresh indexing")

    if tool in {"grep_and_read"}:
        hints.append("fallback: search -> read_file")
    if tool in {"repo_candidates"}:
        hints.append("fallback: list_files with summary=true")
    if tool in {"search_api_endpoints"}:
        hints.append("fix scope using repo or root_ids")
    if tool in {"read_symbol", "get_callers", "get_implementations", "call_graph", "call_graph_health"}:
        hints.append("check if symbol is indexed or run rescan")

    return hints

def require_db_schema(db: Any, tool: str, table: str, columns: List[str]):
    """필요한 테이블이나 컬럼이 누락된 경우 에러 응답을 반환합니다."""
    checker = getattr(db, "has_table_columns", None)
    if not checker:
        return None
    try:
        res = checker(table, columns)
        if not isinstance(res, tuple) or len(res) != 2:
            return None
        ok, missing = res
    except Exception:
        return None
    if ok:
        return None
    msg = f"DB schema mismatch: {table} missing columns: {', '.join(missing)}"
    return mcp_response(
        tool,
        lambda: pack_error(tool, ErrorCode.DB_ERROR, msg),
        lambda: {"error": {"code": ErrorCode.DB_ERROR.value, "message": msg}, "isError": True},
    )

def get_data_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Dict, Pydantic 모델, 또는 일반 객체에서 안전하게 속성값을 가져옵니다."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    # Pydantic v2 또는 표준 객체
    return getattr(obj, attr, default)

def _get_env_any(key: str, default: str = "") -> str:
    """여러 접두사(SARI_, CODEX_, GEMINI_)를 확인하여 환경 변수를 읽습니다."""
    prefixes = ["SARI_", "CODEX_", "GEMINI_", ""]
    for p in prefixes:
        val = os.environ.get(p + key)
        if val is not None:
            return val
    return default

def _get_format() -> str:
    """응답 형식(pack 또는 json)을 결정합니다."""
    fmt = _get_env_any("FORMAT", "pack").lower()
    return "pack" if fmt == "pack" else "json"

def _compact_enabled() -> bool:
    """압축된 JSON 출력 사용 여부를 확인합니다."""
    val = _get_env_any("RESPONSE_COMPACT", "1")
    return val.strip().lower() in ("1", "true", "yes", "on")

# --- Format Selection ---

def mcp_response(
    tool_name: str,
    pack_func: Callable[[], str],
    json_func: Callable[[], Dict[str, Any]]
) -> Dict[str, Any]:
    """
    설정에 따라 PACK1 또는 JSON 형식으로 응답을 분기 처리하는 헬퍼 함수입니다.

    pack_func: PACK1 텍스트 페이로드를 생성하는 함수
    json_func: JSON 직렬화를 위한 딕셔너리를 생성하는 함수
    """
    fmt = _get_format()

    try:
        if fmt == "pack":
            text_output = pack_func()
            return {
                "content": [{"type": "text", "text": text_output}]
            }
        else:
            # JSON 모드 (레거시/디버깅용)
            data = json_func()
            compact = _compact_enabled()
            
            # 표준 MCP 콘텐츠 구조 형성
            json_text = json.dumps(data, ensure_ascii=False, 
                                   separators=(",", ":") if compact else None,
                                   indent=None if compact else 2)

            res = {"content": [{"type": "text", "text": json_text}]}
            # 클라이언트 처리를 쉽게 하기 위해 메타데이터를 최상위 레벨로 올림
            if isinstance(data, dict):
                for k, v in data.items():
                    if k not in res: # MCP 예약 키는 덮어쓰지 않음
                        res[k] = v
            return res
    except Exception as e:
        import traceback
        err_msg = f"Internal Error in {tool_name}: {str(e)}"
        stack = traceback.format_exc()
        logger.error(err_msg, exc_info=True)

        if fmt == "pack":
            return {
                "content": [{"type": "text", "text": pack_error(tool_name, ErrorCode.INTERNAL, err_msg, trace=stack)}],
                "isError": True
            }
        else:
            return {
                "content": [{"type": "text", "text": json.dumps({"error": err_msg, "trace": stack})}],
                "isError": True,
                "error": {"code": ErrorCode.INTERNAL.value, "message": err_msg}
            }


def mcp_json(obj):
    """딕셔너리를 표준 MCP 응답 형식으로 포맷팅하는 유틸리티입니다."""
    if _compact_enabled():
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        payload = json.dumps(obj, ensure_ascii=False, indent=2)
    res = {"content": [{"type": "text", "text": payload}]}
    if isinstance(obj, dict):
        res.update(obj)
    return res


def resolve_root_ids(roots: List[str]) -> List[str]:
    """워크스페이스 경로 목록을 root_id 목록으로 변환합니다."""
    if not roots or not WorkspaceManager:
        return []
    out: List[str] = []
    allow_legacy = str(os.environ.get("SARI_ALLOW_LEGACY", "")).strip().lower() in {"1", "true", "yes", "on"}
    for r in roots:
        try:
            out.append(WorkspaceManager.root_id_for_workspace(r))
            if allow_legacy:
                out.append(WorkspaceManager.root_id(r))
        except Exception:
            continue
    return list(dict.fromkeys(out))

def parse_timestamp(v: Any) -> int:
    """ISO8601 문자열이나 정수를 유닉스 타임스탬프로 견고하게 파싱합니다."""
    if v is None or v == "": return 0
    if isinstance(v, (int, float)): return int(v)
    s = str(v).strip()
    if s.isdigit(): return int(s)
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s).timestamp())
    except Exception: return 0

def parse_search_options(args: Dict[str, Any], roots: List[str]) -> Any:
    """MCP 도구를 위한 표준화된 SearchOptions 파서입니다."""
    from sari.core.models import SearchOptions
    
    root_ids = resolve_root_ids(roots)
    req_root_ids = args.get("root_ids")
    if isinstance(req_root_ids, list):
        req_root_ids = [str(r) for r in req_root_ids if r]
        root_ids = [r for r in root_ids if r in req_root_ids] if root_ids else list(req_root_ids)

    return SearchOptions(
        query=(args.get("query") or "").strip(),
        repo=args.get("scope") or args.get("repo"),
        limit=max(1, min(int(args.get("limit", 8) or 8), 50)),
        offset=max(int(args.get("offset", 0) or 0), 0),
        snippet_lines=min(max(int(args.get("context_lines", 5) or 5), 1), 20),
        file_types=list(args.get("file_types", [])),
        path_pattern=args.get("path_pattern"),
        exclude_patterns=args.get("exclude_patterns", []),
        recency_boost=bool(args.get("recency_boost", False)),
        use_regex=bool(args.get("use_regex", False)),
        case_sensitive=bool(args.get("case_sensitive", False)),
        total_mode=str(args.get("total_mode") or "exact").strip().lower(),
        root_ids=root_ids,
    )


def _intersect_preserve_order(base: List[str], rhs: List[str]) -> List[str]:
    """순서를 유지하면서 두 리스트의 교집합을 반환합니다."""
    rhs_set = set(rhs)
    return [x for x in base if x in rhs_set]


def resolve_repo_scope(
    repo: Optional[str],
    roots: List[str],
    db: Optional[Any] = None,
) -> Tuple[Optional[str], List[str]]:
    """
    repo 인자를 다음과 같이 해석합니다:
    - effective_repo: files.repo 필터에 사용할 값
    - effective_root_ids: 루트 레이블/이름/경로 및 DB 메타데이터로부터 추론된 루트 스코프
    """
    allowed_root_ids = resolve_root_ids(roots)
    repo_raw = str(repo or "").strip()
    if not repo_raw:
        return None, allowed_root_ids

    q = repo_raw.lower()
    allow_legacy = str(os.environ.get("SARI_ALLOW_LEGACY", "")).strip().lower() in {"1", "true", "yes", "on"}
    matched_root_ids: List[str] = []
    for r in roots or []:
        try:
            rp = Path(r).expanduser().resolve()
            name = rp.name.lower()
            full = str(rp).lower()
            if q == name or q == full or (q and q in name):
                matched_root_ids.append(WorkspaceManager.root_id_for_workspace(str(rp)))
                if allow_legacy:
                    matched_root_ids.append(WorkspaceManager.root_id(str(rp)))
        except Exception:
            continue

    db_repo_root_ids: List[str] = []
    db_root_match_ids: List[str] = []
    if db is not None:
        conn = getattr(db, "_read", None)
        if conn is None and hasattr(db, "get_read_connection"):
            try:
                conn = db.get_read_connection()
            except Exception:
                conn = None
        if conn is not None:
            try:
                rows = conn.execute(
                    "SELECT DISTINCT root_id FROM files WHERE LOWER(COALESCE(repo, '')) = LOWER(?)",
                    (repo_raw,),
                ).fetchall()
                db_repo_root_ids = [str(r[0]) for r in rows if r and r[0]]
            except Exception:
                db_repo_root_ids = []
            try:
                rows = conn.execute(
                    "SELECT root_id FROM roots WHERE LOWER(COALESCE(label, '')) = LOWER(?) OR LOWER(COALESCE(root_path, '')) LIKE ?",
                    (repo_raw, f"%/{q}%"),
                ).fetchall()
                db_root_match_ids = [str(r[0]) for r in rows if r and r[0]]
            except Exception:
                db_root_match_ids = []

    if db_root_match_ids:
        matched_root_ids.extend(db_root_match_ids)

    if matched_root_ids:
        if allowed_root_ids:
            return None, _intersect_preserve_order(allowed_root_ids, matched_root_ids)
        return None, list(dict.fromkeys(matched_root_ids))

    if db_repo_root_ids:
        if allowed_root_ids:
            return repo_raw, _intersect_preserve_order(allowed_root_ids, db_repo_root_ids)
        return repo_raw, list(dict.fromkeys(db_repo_root_ids))

    return repo_raw, allowed_root_ids

def _is_safe_relative_path(rel: str) -> bool:
    """상대 경로가 안전한지(디렉토리 이탈 등이 없는지) 확인합니다."""
    if rel is None:
        return False
    rel = str(rel).strip()
    if not rel:
        return False
    p = Path(rel)
    if p.is_absolute():
        return False
    # 상위 디렉토리 참조(..) 및 Windows 드라이브 경로 차단
    for part in p.parts:
        if part in {"..", ""}:
            return False
        if ":" in part:
            return False
    return True


def resolve_db_path(input_path: str, roots: List[str]) -> Optional[str]:
    """
    파일 시스템 경로를 Sari DB 경로(root_id/relative_path)로 변환합니다.
    Longest Prefix Match를 사용하여 중첩된 워크스페이스를 처리합니다.
    """
    if not input_path or not roots or not WorkspaceManager:
        return None

    try:
        # 대상 경로 정규화
        p = Path(os.path.expanduser(input_path)).resolve()
    except Exception:
        return None

    # 모든 루트를 한 번만 해결하여 처리 속도 최적화
    resolved_roots = []
    for r in roots:
        try:
            resolved_roots.append(Path(r).expanduser().resolve())
        except Exception:
            continue

    # 경로 구성 요소 수(깊이)에 따라 내림차순 정렬하여 가장 구체적인 매칭 보장
    sorted_roots = sorted(resolved_roots, key=lambda x: len(x.parts), reverse=True)
    
    for root_path in sorted_roots:
        try:
            # 대상이 이 루트 내부에 있는지 확인
            if p == root_path or root_path in p.parents:
                rel = p.relative_to(root_path).as_posix()
                if not _is_safe_relative_path(rel) and p != root_path:
                    continue
                # root_id는 워크스페이스의 정규화된 절대 경로입니다.
                rid = WorkspaceManager.root_id_for_workspace(str(root_path))
                return f"{rid}/{rel}" if rel != "." else rid
        except Exception:
            continue
    return None


def resolve_fs_path(db_path: str, roots: List[str]) -> Optional[str]:
    """
    Sari DB 경로를 실제 파일 시스템 경로로 다시 변환합니다.
    활성 루트 목록과 매칭하여 확인합니다.
    """
    if not db_path or not roots or not WorkspaceManager:
        return None

    # 모든 활성 루트 ID 정규화
    active_root_map = {}
    for r in roots:
        try:
            rid = WorkspaceManager.root_id_for_workspace(r)
            active_root_map[rid] = Path(r).expanduser().resolve()
        except Exception:
            continue

    # 가장 구체적인 매칭을 위해 루트 ID 길이에 따라 내림차순 정렬
    sorted_rids = sorted(active_root_map.keys(), key=len, reverse=True)

    for rid in sorted_rids:
        if db_path.startswith(rid):
            # rid가 db_path 전체이거나 슬래시로 구분된 접두사일 경우
            if len(db_path) == len(rid):
                return str(active_root_map[rid])
            elif db_path[len(rid)] == "/":
                rel = db_path[len(rid) + 1:]
                if not _is_safe_relative_path(rel):
                    continue
                candidate = (active_root_map[rid] / rel).resolve()
                # 최종 안전 확인: 결과 경로가 여전히 루트 내부에 있는지 보장
                if candidate == active_root_map[rid] or active_root_map[rid] in candidate.parents:
                    return str(candidate)
    
    return None
