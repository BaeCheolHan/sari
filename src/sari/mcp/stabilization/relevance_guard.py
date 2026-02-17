"""read 대상 관련성 평가를 제공한다."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Mapping


EXCLUDED_PARTS = {"vendor", "node_modules", ".git", "dist"}


def _is_excluded(path: str) -> bool:
    """관련성 평가 제외 경로 여부를 반환한다."""
    parts = {part for part in PurePosixPath(path.replace("\\", "/")).parts if part != ""}
    return len(parts.intersection(EXCLUDED_PARTS)) > 0


def assess_relevance(
    mode: str,
    target: str,
    search_context: Mapping[str, object],
) -> tuple[str, list[str], list[str], str | None]:
    """최근 search 컨텍스트 대비 read 타깃 관련성을 평가한다."""
    if mode not in {"file", "diff_preview"}:
        return ("OK", [], [], None)
    normalized_target = str(target or "").strip()
    if normalized_target == "" or _is_excluded(normalized_target):
        return ("OK", [], [], None)
    top_paths_raw = search_context.get("last_search_top_paths", [])
    top_paths = [str(path) for path in top_paths_raw] if isinstance(top_paths_raw, list) else []
    search_count = int(search_context.get("search_count", 0) or 0)
    if search_count <= 0 or len(top_paths) == 0:
        return ("OK", [], [], None)
    if normalized_target in top_paths:
        return ("OK", [], [], None)
    alternatives = top_paths[:3]
    return (
        "LOW_RELEVANCE",
        ["This target seems unrelated to recent search results."],
        alternatives,
        "search",
    )

