from __future__ import annotations

from pathlib import PurePosixPath
from typing import Mapping

EXCLUDED_PARTS = {"vendor", "node_modules", ".git", "dist"}


def _is_excluded(path: str) -> bool:
    parts = {p for p in PurePosixPath(path.replace("\\", "/")).parts if p}
    return bool(parts.intersection(EXCLUDED_PARTS))


def assess_relevance(
    mode: str,
    target: str,
    search_context: Mapping[str, object],
) -> tuple[str, list[str], list[str], str | None]:
    if mode not in {"file", "diff_preview"}:
        return ("OK", [], [], None)

    normalized_target = str(target or "").strip()
    if not normalized_target or _is_excluded(normalized_target):
        return ("OK", [], [], None)

    top_paths_raw = search_context.get("last_search_top_paths", [])
    top_paths = [str(p) for p in top_paths_raw] if isinstance(top_paths_raw, list) else []
    search_count = int(search_context.get("search_count", 0) or 0)
    if search_count <= 0 or not top_paths:
        return ("OK", [], [], None)

    if normalized_target in top_paths:
        return ("OK", [], [], None)

    alternatives = top_paths[:3]
    warnings = ["This target seems unrelated to recent search results."]
    suggested = "search"
    return ("LOW_RELEVANCE", warnings, alternatives, suggested)
