from typing import Any, Dict, List
from sari.mcp.tools._util import resolve_root_ids, mcp_response, pack_header, pack_line, get_data_attr

def execute_list_files(args: Dict[str, Any], db: Any, logger=None, roots: List[str] = None) -> Dict[str, Any]:
    """
    파일 목록 조회 도구.
    중앙화된 DB 접근과 안전한 속성 헬퍼를 사용하여 현대화되었습니다.
    
    repo 인자가 없으면 저장소별 파일 수 요약 정보를 반환하고,
    repo 인자가 있으면 해당 저장소의 파일 상세 목록을 반환합니다.
    """
    limit = int(args.get("limit", 50))
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

    def build_json() -> Dict[str, Any]:
        """JSON 포맷 응답 생성"""
        if not repo:
            stats = db.get_repo_stats(root_ids=root_ids)
            return {"mode": "summary", "stats": stats}
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
