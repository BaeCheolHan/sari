from __future__ import annotations

from typing import Mapping

from sari.core.policy_engine import ReadPolicy, load_read_policy

# Backward-compatible alias for tests/imports.
BudgetPolicy = ReadPolicy


def evaluate_budget_state(
    metrics_snapshot: Mapping[str, object],
    policy: BudgetPolicy | None = None,
) -> tuple[str, list[str], str | None]:
    policy = policy or load_read_policy()
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
    policy: BudgetPolicy | None = None,
) -> tuple[dict[str, object], bool, list[str]]:
    policy = policy or load_read_policy()
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
    elif mode in {"snippet", "diff_preview"}:
        out["max_preview_chars"] = policy.max_preview_chars

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

    if mode == "snippet":
        raw_limit = out.get("max_results", out.get("limit", policy.max_snippet_results))
        try:
            requested = int(raw_limit)
            clamped = max(1, min(requested, policy.max_snippet_results))
            out["limit"] = clamped
            out["max_results"] = clamped
            if requested != clamped:
                degraded = True
                warnings.append("Snippet results reduced due to max_results budget.")
        except Exception:
            out["limit"] = policy.max_snippet_results
            out["max_results"] = policy.max_snippet_results
            degraded = True
            warnings.append("Invalid snippet max_results; applied default budget cap.")

        if "context_lines" in out:
            try:
                requested_context = int(out.get("context_lines"))
                clamped_context = max(0, min(requested_context, policy.max_snippet_context_lines))
                out["context_lines"] = clamped_context
                if requested_context != clamped_context:
                    degraded = True
                    warnings.append("Snippet context_lines reduced due to budget policy.")
            except Exception:
                out["context_lines"] = policy.max_snippet_context_lines
                degraded = True
                warnings.append("Invalid snippet context_lines; applied budget cap.")

    return out, degraded, warnings
