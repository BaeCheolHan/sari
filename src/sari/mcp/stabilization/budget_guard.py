from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class BudgetPolicy:
    max_reads_per_session: int = 25
    max_total_read_lines: int = 2500
    max_single_read_lines: int = 300
    max_preview_chars: int = 12000


def evaluate_budget_state(
    metrics_snapshot: Mapping[str, object],
    policy: BudgetPolicy = BudgetPolicy(),
) -> tuple[str, list[str], str | None]:
    reads_count = int(metrics_snapshot.get("reads_count", 0) or 0)
    reads_lines_total = int(metrics_snapshot.get("reads_lines_total", 0) or 0)
    warnings: list[str] = []

    if reads_count >= policy.max_reads_per_session or reads_lines_total >= policy.max_total_read_lines:
        return (
            "HARD_LIMIT",
            ["Read budget exceeded. Use search to narrow scope."],
            "search",
        )

    if reads_count >= int(policy.max_reads_per_session * 0.8):
        warnings.append("Read budget near limit. Narrow scope with search before broad reads.")
    if reads_lines_total >= int(policy.max_total_read_lines * 0.8):
        warnings.append("Total read lines near budget. Prefer targeted reads.")

    return ("NORMAL", warnings, "read" if not warnings else "search")


def apply_soft_limits(
    mode: str,
    delegated_args: dict[str, object],
    policy: BudgetPolicy = BudgetPolicy(),
) -> tuple[dict[str, object], bool, list[str]]:
    out = dict(delegated_args)
    warnings: list[str] = []
    degraded = False

    if "max_preview_chars" in out:
        try:
            requested = int(out.get("max_preview_chars"))
            if requested > policy.max_preview_chars:
                out["max_preview_chars"] = policy.max_preview_chars
                degraded = True
                warnings.append("Preview reduced due to max_preview_chars budget.")
        except Exception:
            pass

    if mode == "file":
        raw_limit = out.get("limit")
        if raw_limit is None:
            out["limit"] = policy.max_single_read_lines
            degraded = True
            warnings.append("Applied default read limit to control payload size.")
        else:
            try:
                limit = int(raw_limit)
                if limit > policy.max_single_read_lines:
                    out["limit"] = policy.max_single_read_lines
                    degraded = True
                    warnings.append("Read limit reduced due to budget policy.")
            except Exception:
                pass

    return out, degraded, warnings
