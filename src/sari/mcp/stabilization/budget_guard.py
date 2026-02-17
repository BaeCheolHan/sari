"""read budget 평가/소프트 제한 적용 유틸을 제공한다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ReadPolicy:
    """read stabilization 정책을 표현한다."""

    max_reads_per_session: int = 25
    max_total_read_lines: int = 2500
    max_single_read_lines: int = 300
    max_preview_chars: int = 12000
    max_snippet_results: int = 20
    max_snippet_context_lines: int = 20


def _to_int(raw_value: str, fallback: int, minimum: int) -> int:
    """정수 환경변수를 파싱한다."""
    try:
        parsed = int(raw_value)
    except ValueError:
        parsed = fallback
    return max(minimum, parsed)


def load_read_policy() -> ReadPolicy:
    """환경변수 기반 read policy를 구성한다."""
    return ReadPolicy(
        max_reads_per_session=_to_int(os.getenv("SARI_READ_MAX_READS_PER_SESSION", "25"), 25, 1),
        max_total_read_lines=_to_int(os.getenv("SARI_READ_MAX_TOTAL_LINES", "2500"), 2500, 1),
        max_single_read_lines=_to_int(os.getenv("SARI_READ_MAX_SINGLE_READ_LINES", "300"), 300, 1),
        max_preview_chars=_to_int(os.getenv("SARI_READ_MAX_PREVIEW_CHARS", "12000"), 12000, 100),
        max_snippet_results=_to_int(os.getenv("SARI_READ_MAX_SNIPPET_RESULTS", "20"), 20, 1),
        max_snippet_context_lines=_to_int(os.getenv("SARI_READ_MAX_SNIPPET_CONTEXT_LINES", "20"), 20, 0),
    )


def evaluate_budget_state(
    metrics_snapshot: Mapping[str, object],
    policy: ReadPolicy | None = None,
) -> tuple[str, list[str], str | None]:
    """세션 누적 메트릭 기반 budget 상태를 평가한다."""
    effective_policy = load_read_policy() if policy is None else policy
    reads_count = int(metrics_snapshot.get("reads_count", 0) or 0)
    reads_lines_total = int(metrics_snapshot.get("reads_lines_total", 0) or 0)
    warnings: list[str] = []
    if reads_count >= effective_policy.max_reads_per_session or reads_lines_total >= effective_policy.max_total_read_lines:
        return ("HARD_LIMIT", ["Read budget exceeded. Use search to narrow scope."], "search")
    if reads_count >= int(effective_policy.max_reads_per_session * 0.8):
        warnings.append("Read budget near limit. Narrow scope with search before broad reads.")
    if reads_lines_total >= int(effective_policy.max_total_read_lines * 0.8):
        warnings.append("Total read lines near budget. Prefer targeted reads.")
    return ("NORMAL", warnings, "read" if len(warnings) == 0 else "search")


def apply_soft_limits(
    mode: str,
    delegated_args: dict[str, object],
    policy: ReadPolicy | None = None,
) -> tuple[dict[str, object], bool, list[str]]:
    """요청 인자에 소프트 제한을 적용한다."""
    effective_policy = load_read_policy() if policy is None else policy
    out = dict(delegated_args)
    warnings: list[str] = []
    degraded = False

    if "max_preview_chars" in out:
        preview_raw = out.get("max_preview_chars")
        if isinstance(preview_raw, int) and preview_raw > effective_policy.max_preview_chars:
            out["max_preview_chars"] = effective_policy.max_preview_chars
            warnings.append("Preview reduced due to max_preview_chars budget.")
            degraded = True
    elif mode in {"snippet", "diff_preview"}:
        out["max_preview_chars"] = effective_policy.max_preview_chars

    if mode == "file":
        raw_limit = out.get("limit")
        if raw_limit is None:
            out["limit"] = effective_policy.max_single_read_lines
            warnings.append("Applied default read limit to control payload size.")
            degraded = True
        elif isinstance(raw_limit, int) and raw_limit > effective_policy.max_single_read_lines:
            out["limit"] = effective_policy.max_single_read_lines
            warnings.append("Read limit reduced due to budget policy.")
            degraded = True

    if mode == "snippet":
        raw_limit = out.get("limit", effective_policy.max_snippet_results)
        if isinstance(raw_limit, int):
            clamped = max(1, min(raw_limit, effective_policy.max_snippet_results))
            out["limit"] = clamped
            if clamped != raw_limit:
                warnings.append("Snippet results reduced due to max_results budget.")
                degraded = True
        else:
            out["limit"] = effective_policy.max_snippet_results
            warnings.append("Invalid snippet max_results; applied default budget cap.")
            degraded = True

        raw_context = out.get("context_lines")
        if isinstance(raw_context, int):
            clamped_context = max(0, min(raw_context, effective_policy.max_snippet_context_lines))
            out["context_lines"] = clamped_context
            if clamped_context != raw_context:
                warnings.append("Snippet context_lines reduced due to budget policy.")
                degraded = True

    return out, degraded, warnings

