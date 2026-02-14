from collections.abc import Mapping
import os
from typing import TypeAlias

from sari.mcp.tools._util import (
    resolve_root_ids,
    mcp_response,
    pack_header,
    pack_line,
    get_data_attr,
    parse_int_arg,
    invalid_args_response,
    require_repo_arg,
)

ToolResult: TypeAlias = dict[str, object]


def execute_list_files(
    args: object,
    db: object,
    logger: object = None,
    roots: list[str] | None = None,
) -> ToolResult:
    """List indexed files for a required repository scope."""
    del logger
    if not isinstance(args, Mapping):
        return invalid_args_response("list_files", "args must be an object")

    enforce_repo = bool(args.get("__enforce_repo__", False))
    if enforce_repo:
        repo_err = require_repo_arg(args, "list_files")
        if repo_err:
            return repo_err

    limit, err = parse_int_arg(args, "limit", 50, "list_files", min_value=1)
    if err:
        return err
    if limit is None:
        return invalid_args_response("list_files", "'limit' must be an integer")

    root_ids = resolve_root_ids(roots or [])
    repo = str(args.get("repo", "")).strip()
    if not repo:
        summary_only = "limit" not in args
        # Legacy summary mode for direct function calls (tests/backward-compat).
        if summary_only:
            fmt = str(os.environ.get("SARI_FORMAT", "pack")).strip().lower()
            if fmt == "json":
                return invalid_args_response("list_files", "repo is required")

            def _summary_pack() -> str:
                stats = db.get_repo_stats(root_ids=root_ids) if hasattr(db, "get_repo_stats") else {}
                lines = [pack_header("list_files", {"mode": "summary"}, returned=len(stats))]
                for k, v in (stats or {}).items():
                    lines.append(pack_line("r", {"repo": str(k), "file_count": str(v)}))
                lines.append("m:warning=code=INVALID_ARGS msg=repo%20is%20required")
                return "\n".join(lines)

            def _summary_json() -> ToolResult:
                stats = db.get_repo_stats(root_ids=root_ids) if hasattr(db, "get_repo_stats") else {}
                return {"mode": "summary", "repo_stats": dict(stats or {})}

            return mcp_response("list_files", _summary_pack, _summary_json)

    def build_pack() -> str:
        files = db.list_files(limit=limit, repo=repo, root_ids=root_ids)
        header = pack_header("list_files", {"repo": repo}, returned=len(files))
        lines = [header]
        for f in files:
            lines.append(
                pack_line(
                    "f",
                    {
                        "path": get_data_attr(f, "path"),
                        "size": str(get_data_attr(f, "size")),
                        "repo": get_data_attr(f, "repo"),
                    },
                )
            )
        return "\n".join(lines)

    def build_json() -> ToolResult:
        files = db.list_files(limit=limit, repo=repo, root_ids=root_ids)
        results = []
        for f in files:
            results.append(
                {
                    "path": get_data_attr(f, "path"),
                    "size": get_data_attr(f, "size"),
                    "repo": get_data_attr(f, "repo"),
                }
            )
        return {"repo": repo, "files": results}

    return mcp_response("list_files", build_pack, build_json)
