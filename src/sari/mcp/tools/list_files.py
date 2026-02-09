from typing import Any, Dict, List
from sari.mcp.tools._util import resolve_root_ids, mcp_response, pack_header, pack_line, get_data_attr

def execute_list_files(args: Dict[str, Any], db: "LocalSearchDB", logger=None, roots: List[str] = None) -> Dict[str, Any]:
    """Modernized List Files Tool: Uses centralized DB access and safe attribute helpers."""
    limit = int(args.get("limit", 50))
    repo = args.get("repo")
    root_ids = resolve_root_ids(roots or [])

    def build_pack() -> str:
        if not repo:
            # Summary mode
            stats = db.get_repo_stats(root_ids=root_ids)
            header = pack_header("list_files", {"mode": "summary"}, returned=len(stats))
            lines = [header]
            for r, count in stats.items():
                lines.append(pack_line("r", {"repo": r, "file_count": str(count)}))
            
            if len(stats) == 1:
                files = db.list_files(limit=5, root_ids=root_ids)
                for f in files:
                    lines.append(pack_line("f", {"path": get_data_attr(f, "path"), "repo": get_data_attr(f, "repo")}))
        else:
            # Detail mode
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
