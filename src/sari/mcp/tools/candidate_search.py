from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sari.mcp.tools._util import internal_error_response, parse_search_options


def execute_candidate_search_raw(args: dict[str, object], db: Any, roots: list[str]) -> dict[str, object]:
    """Repo-scoped candidate search (DB/FTS layer)."""
    try:
        opts = parse_search_options(args, roots, db=db)
        search_fn = getattr(db, "search", None)
        if not callable(search_fn):
            raise RuntimeError("No search backend available (search)")
        hits, meta = search_fn(opts)
        if hits is None:
            normalized_hits: list[object] = []
        elif isinstance(hits, Mapping):
            normalized_hits = [hits]
        elif isinstance(hits, (list, tuple)):
            normalized_hits = list(hits)
        else:
            try:
                normalized_hits = list(hits)
            except TypeError:
                normalized_hits = []
        results = []
        for h in normalized_hits:
            if isinstance(h, Mapping):
                path = h.get("path", "")
                repo = h.get("repo", "")
                score = h.get("score", 0.0)
                snippet = h.get("snippet", "")
                mtime = h.get("mtime", 0)
                size = h.get("size", 0)
                file_type = h.get("file_type", "")
                hit_reason = h.get("hit_reason", "")
            else:
                path = getattr(h, "path", "")
                repo = getattr(h, "repo", "")
                score = getattr(h, "score", 0.0)
                snippet = getattr(h, "snippet", "")
                mtime = getattr(h, "mtime", 0)
                size = getattr(h, "size", 0)
                file_type = getattr(h, "file_type", "")
                hit_reason = getattr(h, "hit_reason", "")
            results.append(
                {
                    "path": path,
                    "repo": repo,
                    "score": score,
                    "snippet": snippet,
                    "mtime": mtime,
                    "size": size,
                    "file_type": file_type,
                    "hit_reason": hit_reason,
                }
            )
        return {"results": results, "meta": meta}
    except Exception as e:
        return internal_error_response(
            "search",
            e,
            reason_code="SEARCH_EXECUTION_FAILED",
            data={
                "search_type": str(args.get("search_type", "code")).lower(),
                "query": str(args.get("query", ""))[:120],
            },
            fallback_message="Search failed",
        )

