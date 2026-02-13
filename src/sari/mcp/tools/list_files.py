from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias

from sari.mcp.tools._util import (
    resolve_root_ids,
    mcp_response,
    pack_header,
    pack_line,
    get_data_attr,
    parse_int_arg,
    invalid_args_response,
)

ToolResult: TypeAlias = dict[str, object]


def _infer_top_directory(path: str, rel_path: str | None = None, roots: list[str] | None = None) -> str:
    rel = str(rel_path or "").strip().replace("\\", "/")
    if not rel:
        p = str(path or "").strip().replace("\\", "/")
        if p:
            candidate = p
            try:
                p_obj = Path(p)
                if p_obj.is_absolute() and roots:
                    for r in roots:
                        try:
                            rel_from_root = str(p_obj.relative_to(Path(str(r)).resolve())).replace("\\", "/")
                            if rel_from_root:
                                candidate = rel_from_root
                                break
                        except Exception:
                            continue
            except Exception:
                pass
            rel = candidate.lstrip("./")
    rel = rel.lstrip("./")
    if not rel:
        return "(root)"
    parts = [seg for seg in rel.split("/") if seg]
    if not parts:
        return "(root)"
    if len(parts) == 1:
        return "(root)"
    common_dirs = {"src", "app", "lib", "tests", "test", "docs", "scripts", "cmd", "pkg", "internal"}
    start_idx = 0
    if len(parts) >= 3 and parts[0] not in common_dirs and not parts[0].startswith("."):
        start_idx = 1
    top = parts[start_idx]
    return top or "(root)"


def _build_directory_aggregates(files: list[dict[str, object]], roots: list[str]) -> list[dict[str, object]]:
    buckets: dict[str, int] = {}
    for f in files:
        top = _infer_top_directory(
            str(get_data_attr(f, "path", "") or ""),
            str(get_data_attr(f, "rel_path", "") or ""),
            roots,
        )
        buckets[top] = int(buckets.get(top, 0) or 0) + 1
    return [{"dir": d, "file_count": c} for d, c in sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0]))]


def execute_list_files(
    args: object,
    db: object,
    logger: object = None,
    roots: list[str] | None = None,
) -> ToolResult:
    """
    파일 목록 조회 도구.
    중앙화된 DB 접근과 안전한 속성 헬퍼를 사용하여 현대화되었습니다.
    
    repo 인자가 없으면 저장소별 파일 수 요약 정보를 반환하고,
    repo 인자가 있으면 해당 저장소의 파일 상세 목록을 반환합니다.
    """
    if not isinstance(args, Mapping):
        return invalid_args_response("list_files", "args must be an object")

    limit, err = parse_int_arg(args, "limit", 50, "list_files", min_value=1)
    if err:
        return err
    if limit is None:
        return invalid_args_response("list_files", "'limit' must be an integer")
    repo = args.get("repo")
    root_ids = resolve_root_ids(roots or [])

    def build_pack() -> str:
        if not repo:
            # 요약 모드 (Summary mode)
            stats = db.get_repo_stats(root_ids=root_ids)
            header = pack_header("list_files", {"mode": "summary"}, returned=len(stats))
            lines = [header]
            for r, count in stats.items():
                lines.append(pack_line("r", {"repo": r, "file_count": str(count)}))
            
            # 레포지토리가 하나뿐이면 상위 5개 파일 미리보기 제공
            if len(stats) == 1:
                files = db.list_files(limit=5, root_ids=root_ids)
                directories = _build_directory_aggregates(files, roots or [])
                for d in directories[:10]:
                    lines.append(pack_line("d", {"dir": d["dir"], "file_count": str(d["file_count"])}))
                for f in files:
                    lines.append(pack_line("f", {"path": get_data_attr(f, "path"), "repo": get_data_attr(f, "repo")}))
        else:
            # 상세 모드 (Detail mode)
            files = db.list_files(limit=limit, repo=repo, root_ids=root_ids)
            header = pack_header("list_files", {"repo": repo}, returned=len(files))
            lines = [header]
            for f in files:
                lines.append(pack_line("f", {
                    "path": get_data_attr(f, "path"),
                    "size": str(get_data_attr(f, "size")),
                    "repo": get_data_attr(f, "repo")
                }))
            lines.append(pack_line("m", {"hint": "repo 필터는 정확히 일치해야 합니다."}))
        return "\n".join(lines)

    def build_json() -> ToolResult:
        """JSON 포맷 응답 생성"""
        if not repo:
            stats = db.get_repo_stats(root_ids=root_ids)
            files = db.list_files(limit=5, root_ids=root_ids) if len(stats) == 1 else []
            directories = _build_directory_aggregates(files, roots or []) if files else []
            return {"mode": "summary", "stats": stats, "directories": directories}
        else:
            files = db.list_files(limit=limit, repo=repo, root_ids=root_ids)
            results = []
            for f in files:
                results.append({
                    "path": get_data_attr(f, "path"),
                    "size": get_data_attr(f, "size"),
                    "repo": get_data_attr(f, "repo")
                })
            return {"repo": repo, "files": results}

    return mcp_response("list_files", build_pack, build_json)
