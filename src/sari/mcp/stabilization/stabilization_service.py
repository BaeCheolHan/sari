"""MCP stabilization 공통 로직을 서비스로 제공한다."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sari.mcp.stabilization.aggregation import add_read_to_bundle
from sari.mcp.stabilization.budget_guard import apply_soft_limits, evaluate_budget_state
from sari.mcp.stabilization.reason_codes import ReasonCode
from sari.mcp.stabilization.relevance_guard import assess_relevance
from sari.mcp.stabilization.session_state import (
    get_metrics_snapshot,
    get_search_context,
    get_session_key,
    record_read_metrics,
    record_search_metrics,
    requires_strict_session_id,
)
from sari.mcp.stabilization.warning_sink import warn
from sari.mcp.tools.tool_common import content_hash


@dataclass(frozen=True)
class StabilizationPrecheckResult:
    """stabilization 사전 점검 결과 DTO."""

    blocked: bool
    error_code: str | None = None
    error_message: str | None = None
    meta: dict[str, object] | None = None


class StabilizationService:
    """read/search stabilization 정책 적용을 담당한다."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = bool(enabled)

    def apply_soft_limits(self, mode: str, delegated_args: dict[str, object]) -> tuple[dict[str, object], bool, list[str]]:
        """모드별 soft-limit 정책을 적용한다."""
        return apply_soft_limits(mode=mode, delegated_args=delegated_args)

    def precheck_read_call(self, arguments: dict[str, object], repo_root: str) -> StabilizationPrecheckResult:
        """read 진입 전 strict-session/budget hard-limit을 점검한다."""
        if not self._enabled:
            return StabilizationPrecheckResult(blocked=False)
        if requires_strict_session_id(arguments):
            return StabilizationPrecheckResult(
                blocked=True,
                error_code="ERR_SESSION_ID_REQUIRED",
                error_message="session_id is required by strict session policy.",
                meta={
                    "budget_state": "NORMAL",
                    "suggested_next_action": "read",
                    "warnings": ["Provide session_id or disable strict mode."],
                    "reason_codes": [ReasonCode.SESSION_ID_REQUIRED.value],
                    "next_calls": [],
                    "metrics_snapshot": get_metrics_snapshot(arguments, [repo_root]),
                },
            )
        pre_metrics = get_metrics_snapshot(arguments, [repo_root])
        budget_state, budget_warnings, suggested_next_action = evaluate_budget_state(pre_metrics)
        if budget_state == "HARD_LIMIT":
            return StabilizationPrecheckResult(
                blocked=True,
                error_code="ERR_BUDGET_HARD_LIMIT",
                error_message="read budget exceeded. use search first.",
                meta={
                    "budget_state": budget_state,
                    "suggested_next_action": suggested_next_action or "search",
                    "warnings": budget_warnings,
                    "reason_codes": [ReasonCode.BUDGET_HARD_LIMIT.value],
                    "next_calls": [{"tool": "search", "arguments": {"query": "target", "limit": 5}}],
                    "metrics_snapshot": pre_metrics,
                },
            )
        return StabilizationPrecheckResult(blocked=False)

    def build_read_success_meta(
        self,
        *,
        arguments: dict[str, object],
        repo_root: str,
        mode: str,
        target: str,
        content_text: str,
        read_lines: int,
        read_span: int,
        warnings: list[str],
        degraded: bool,
    ) -> dict[str, object] | None:
        """read 성공 응답용 stabilization 메타를 생성한다."""
        if not self._enabled:
            return None
        metrics_snapshot = record_read_metrics(
            arguments,
            [repo_root],
            read_lines=read_lines,
            read_chars=len(content_text),
            read_span=read_span,
        )
        budget_state, budget_warnings, suggested_next_action = evaluate_budget_state(metrics_snapshot)
        search_context = get_search_context(arguments, [repo_root])
        relevance_state, relevance_warnings, relevance_alternatives, relevance_suggested = assess_relevance(
            mode=mode,
            target=target,
            search_context=search_context,
        )
        session_key = get_session_key(arguments, [repo_root])
        bundle_info = add_read_to_bundle(
            session_key=session_key,
            mode=mode,
            path=target,
            text=content_text,
        )
        reason_codes: list[str] = []
        if degraded:
            reason_codes.append(ReasonCode.BUDGET_SOFT_LIMIT.value)
        if relevance_state == "LOW_RELEVANCE":
            reason_codes.append(ReasonCode.LOW_RELEVANCE_OUTSIDE_TOPK.value)
        all_warnings = [*warnings, *budget_warnings, *relevance_warnings]
        next_calls = self._next_calls_for_read(mode=mode, target=target, alternatives=relevance_alternatives)
        suggested = relevance_suggested or suggested_next_action or "read"
        return {
            "budget_state": budget_state,
            "suggested_next_action": suggested,
            "warnings": all_warnings,
            "reason_codes": reason_codes,
            "bundle_id": str(bundle_info.get("context_bundle_id") or ""),
            "next_calls": next_calls,
            "metrics_snapshot": metrics_snapshot,
            "evidence_refs": [
                {
                    "kind": mode,
                    "path": target,
                    "content_hash": content_hash(content_text),
                }
            ],
        }

    def build_search_success_meta(
        self,
        *,
        arguments: dict[str, object],
        repo: str,
        query: str,
        items: list[object],
        degraded: bool,
        fatal_error: bool,
        errors: list[dict[str, object]],
    ) -> dict[str, object] | None:
        """search 응답용 stabilization 메타를 생성한다."""
        if not self._enabled:
            return None
        top_paths = [str(getattr(item, "relative_path", "") or "") for item in items[:10]]
        candidates = self._candidate_mapping(query=query, items=items)
        generated_bundle_id = self._bundle_id(query=query, paths=top_paths)
        metrics_snapshot = record_search_metrics(
            arguments,
            [repo],
            preview_degraded=degraded,
            query=query,
            top_paths=top_paths,
            candidates=candidates,
            bundle_id=generated_bundle_id,
        )
        warnings: list[str] = []
        reason_codes: list[str] = []
        if degraded:
            warnings.append("Search completed with degraded backend state; inspect meta.errors.")
            reason_codes.append(ReasonCode.SEARCH_DEGRADED.value)
        if fatal_error:
            warnings.append("Search failed with fatal backend errors.")
            reason_codes.append(ReasonCode.SEARCH_FATAL.value)
        for error in errors:
            message = str(error.get("message", "")).strip()
            severity = str(error.get("severity", "")).strip().upper()
            code = str(error.get("code", "")).strip()
            if severity == "FATAL":
                warn(f"[search:fatal] {code}: {message}")
            elif message != "":
                warn(f"[search:degraded] {code}: {message}")
        return {
            "budget_state": "NORMAL",
            "suggested_next_action": "read" if len(items) > 0 else "search",
            "warnings": warnings,
            "reason_codes": reason_codes,
            "bundle_id": generated_bundle_id,
            "next_calls": self._next_calls_for_search(items),
            "metrics_snapshot": metrics_snapshot,
            "degraded": degraded,
            "fatal_error": fatal_error,
        }

    def _next_calls_for_read(self, mode: str, target: str, alternatives: list[str]) -> list[dict[str, object]]:
        """read 응답의 다음 호출 힌트를 생성한다."""
        if mode == "symbol":
            return [{"tool": "search", "arguments": {"query": target, "limit": 5}}]
        if len(alternatives) > 0:
            return [{"tool": "read", "arguments": {"mode": "file", "target": alternatives[0]}}]
        return [{"tool": "search", "arguments": {"query": target, "limit": 5}}]

    def _candidate_mapping(self, query: str, items: list[object]) -> dict[str, str]:
        """검색 결과에서 candidate_id 매핑을 생성한다."""
        mapping: dict[str, str] = {}
        for index, item in enumerate(items):
            relative_path = str(getattr(item, "relative_path", "") or "")
            name = str(getattr(item, "name", "") or "")
            raw = f"{query}|{relative_path}|{name}|{index}"
            candidate_key = "cand_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
            mapping[candidate_key] = relative_path
        return mapping

    def _bundle_id(self, query: str, paths: list[str]) -> str:
        """검색 응답 번들 식별자를 생성한다."""
        merged = "\n".join([query, *paths])
        return "bundle_" + hashlib.sha256(merged.encode("utf-8")).hexdigest()[:12]

    def _next_calls_for_search(self, items: list[object]) -> list[dict[str, object]]:
        """다음 권장 호출 힌트를 생성한다."""
        calls: list[dict[str, object]] = []
        for item in items[:3]:
            item_type = str(getattr(item, "item_type", "") or "")
            relative_path = str(getattr(item, "relative_path", "") or "")
            name = str(getattr(item, "name", "") or "")
            if item_type == "symbol":
                calls.append({"tool": "read", "arguments": {"mode": "symbol", "target": name, "path": relative_path}})
            else:
                calls.append({"tool": "read", "arguments": {"mode": "file", "target": relative_path}})
        return calls
