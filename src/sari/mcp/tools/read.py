from collections.abc import Mapping
import json
import os
from typing import TypeAlias

from sari.mcp.stabilization.aggregation import add_read_to_bundle
from sari.mcp.stabilization.budget_guard import apply_soft_limits, evaluate_budget_state
from sari.mcp.stabilization.relevance_guard import assess_relevance
from sari.mcp.stabilization.reason_codes import ReasonCode
from sari.mcp.stabilization.session_state import (
    get_metrics_snapshot,
    get_search_context,
    get_session_key,
    record_read_metrics,
    requires_strict_session_id,
)
from sari.mcp.tools.dry_run_diff import execute_dry_run_diff
from sari.mcp.tools.get_snippet import execute_get_snippet
from sari.mcp.tools.read_file import execute_read_file
from sari.mcp.tools.read_symbol import execute_read_symbol
from sari.mcp.tools._util import (
    ErrorCode,
    invalid_args_response,
    mcp_response,
    pack_error,
)

ToolResult: TypeAlias = dict[str, object]

_MODES = {"file", "symbol", "snippet", "diff_preview"}
_DIFF_BASELINES = {"HEAD", "WORKTREE", "INDEX"}


def _invalid_mode_param(param: str, mode: str) -> ToolResult:
    msg = f"{param} is only valid for mode='{mode}'. Remove it or switch mode."
    return mcp_response(
        "read",
        lambda: pack_error("read", ErrorCode.INVALID_ARGS, msg),
        lambda: {
            "error": {
                "code": ErrorCode.INVALID_ARGS.value,
                "message": msg,
            },
            "isError": True,
        },
    )


def _line_count(text: str) -> int:
    return len(text.splitlines()) if text else 0


def _env_any(key: str, default: str = "") -> str:
    for prefix in ("SARI_", "CODEX_", "GEMINI_", ""):
        value = os.environ.get(prefix + key)
        if value is not None:
            return value
    return default


def _compact_enabled() -> bool:
    return _env_any("RESPONSE_COMPACT", "1").strip().lower() in {"1", "true", "yes", "on"}


def _extract_json_payload(response: ToolResult) -> dict[str, object] | None:
    content = response.get("content")
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, Mapping):
        return None
    text = str(first.get("text", "")).strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _derive_read_metrics(mode: str, payload: Mapping[str, object]) -> tuple[int, int, int]:
    if mode == "file":
        items = payload.get("content", [])
        read_text = ""
        if isinstance(items, list) and items and isinstance(items[0], Mapping):
            read_text = str(items[0].get("text", ""))
        lines = _line_count(read_text)
        return lines, len(read_text), lines

    if mode == "symbol":
        read_text = str(payload.get("content", ""))
        lines = _line_count(read_text)
        start_line = payload.get("start_line")
        end_line = payload.get("end_line")
        span = lines
        if isinstance(start_line, int) and isinstance(end_line, int) and start_line > 0 and end_line >= start_line:
            span = end_line - start_line + 1
        return lines, len(read_text), span

    if mode == "snippet":
        total_lines = 0
        total_chars = 0
        total_span = 0
        results = payload.get("results", [])
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, Mapping):
                    continue
                snippet_text = str(result.get("content", ""))
                lines = _line_count(snippet_text)
                total_lines += lines
                total_chars += len(snippet_text)
                start_line = result.get("start_line")
                end_line = result.get("end_line")
                if isinstance(start_line, int) and isinstance(end_line, int) and start_line > 0 and end_line >= start_line:
                    total_span += end_line - start_line + 1
                else:
                    total_span += lines
        return total_lines, total_chars, total_span

    diff_text = str(payload.get("diff", ""))
    lines = _line_count(diff_text)
    return lines, len(diff_text), lines


def _inject_stabilization(
    response: ToolResult,
    *,
    budget_state: str,
    warnings: list[str],
    suggested_next_action: str | None,
    metrics_snapshot: Mapping[str, float | int],
    reason_codes: list[str],
    extra: Mapping[str, object] | None = None,
) -> ToolResult:
    payload = _extract_json_payload(response)
    if payload is None:
        return response
    meta = payload.get("meta")
    meta_dict = dict(meta) if isinstance(meta, Mapping) else {}
    stabilization = meta_dict.get("stabilization")
    stabilization_dict = dict(stabilization) if isinstance(stabilization, Mapping) else {}
    stabilization_dict["budget_state"] = budget_state
    stabilization_dict["warnings"] = list(warnings)
    stabilization_dict["suggested_next_action"] = suggested_next_action or "search"
    stabilization_dict["metrics_snapshot"] = dict(metrics_snapshot)
    stabilization_dict["reason_codes"] = list(dict.fromkeys(reason_codes))
    if extra:
        stabilization_dict.update(dict(extra))
    meta_dict["stabilization"] = stabilization_dict
    payload["meta"] = meta_dict

    response["content"][0]["text"] = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":") if _compact_enabled() else None,
        indent=None if _compact_enabled() else 2,
    )
    response["meta"] = meta_dict
    return response


def _budget_exceeded_response() -> ToolResult:
    msg = "Read budget exceeded. Use search to narrow scope: run search before additional reads."
    return mcp_response(
        "read",
        lambda: pack_error("read", "BUDGET_EXCEEDED", msg),
        lambda: {
            "error": {
                "code": "BUDGET_EXCEEDED",
                "message": msg,
            },
            "meta": {
                "stabilization": {
                    "reason_codes": [ReasonCode.BUDGET_HARD_LIMIT.value],
                    "suggested_next_action": "search",
                    "warnings": [msg],
                }
            },
            "isError": True,
        },
    )


def _gate_mode() -> str:
    mode = str(os.environ.get("SARI_READ_GATE_MODE", "enforce")).strip().lower()
    return mode if mode in {"enforce", "warn"} else "enforce"


def _max_range_lines() -> int:
    try:
        return max(1, int(os.environ.get("SARI_READ_MAX_RANGE_LINES", "200")))
    except Exception:
        return 200


def _to_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_precision_read(args_map: Mapping[str, object], *, max_range_lines: int) -> tuple[bool, bool]:
    target = str(args_map.get("target") or args_map.get("path") or "").strip()
    if not target:
        return (False, False)

    start_line = _to_int(args_map.get("start_line"))
    end_line = _to_int(args_map.get("end_line"))
    if start_line is not None and end_line is not None and start_line > 0 and end_line >= start_line:
        span = end_line - start_line + 1
        return (span <= max_range_lines, span > max_range_lines)

    offset = _to_int(args_map.get("offset"))
    limit = _to_int(args_map.get("limit"))
    if offset is not None and limit is not None and offset >= 0 and limit > 0:
        return (limit <= max_range_lines, limit > max_range_lines)

    return (False, False)


def _stabilization_error(
    *,
    code: str,
    message: str,
    reason_codes: list[str],
    warnings: list[str] | None = None,
    next_calls: list[dict[str, object]] | None = None,
) -> ToolResult:
    return mcp_response(
        "read",
        lambda: pack_error("read", code, message),
        lambda: {
            "error": {"code": code, "message": message},
            "meta": {
                "stabilization": {
                    "reason_codes": reason_codes,
                    "warnings": list(warnings or []),
                    "suggested_next_action": "search",
                    "next_calls": list(next_calls or []),
                }
            },
            "isError": True,
        },
    )


def _build_search_next_calls(target: str) -> list[dict[str, object]]:
    q = str(target or "").strip()
    if "/" in q:
        q = q.rsplit("/", 1)[-1]
    return [
        {
            "tool": "search",
            "arguments": {"query": q or "target", "search_type": "code", "limit": 5},
        }
    ]


def _enforce_search_ref_gate(
    mode: str,
    args_map: Mapping[str, object],
    search_context: Mapping[str, object],
) -> tuple[bool, ToolResult | None, list[str], list[str]]:
    if mode == "snippet":
        return (True, None, [], [])
    max_lines = _max_range_lines()
    precision_allowed, precision_overflow = _is_precision_read(args_map, max_range_lines=max_lines)
    if precision_allowed:
        return (True, None, [], [])
    if precision_overflow:
        msg = (
            f"Precision read range exceeds max_range_lines={max_lines}. "
            "Split into smaller windows or use search-based candidate read."
        )
        return (
            False,
            _stabilization_error(
                code=ReasonCode.SEARCH_REF_REQUIRED.value,
                message=msg,
                reason_codes=[ReasonCode.SEARCH_REF_REQUIRED.value],
                warnings=[msg],
                next_calls=_build_search_next_calls(str(args_map.get("target") or "")),
            ),
            [ReasonCode.SEARCH_REF_REQUIRED.value],
            [msg],
        )

    candidates_raw = search_context.get("last_search_candidates", {})
    candidates = dict(candidates_raw) if isinstance(candidates_raw, Mapping) else {}
    candidate_id = str(args_map.get("candidate_id") or "").strip()
    target = str(args_map.get("target") or args_map.get("path") or "").strip()
    search_count = int(search_context.get("search_count", 0) or 0)

    if candidate_id:
        matched = str(candidates.get(candidate_id) or "").strip()
        path_arg = str(args_map.get("path") or "").strip()
        if matched and (not target or target == matched or path_arg == matched):
            return (True, None, [], [])
        message = "Candidate ref is invalid for this session target. Use search and retry with returned candidate_id."
        return (
            False,
            _stabilization_error(
                code=ReasonCode.CANDIDATE_REF_REQUIRED.value,
                message=message,
                reason_codes=[ReasonCode.CANDIDATE_REF_REQUIRED.value],
                warnings=[message],
                next_calls=_build_search_next_calls(target),
            ),
            [ReasonCode.CANDIDATE_REF_REQUIRED.value],
            [message],
        )

    reason = ReasonCode.SEARCH_FIRST_REQUIRED if search_count <= 0 else ReasonCode.SEARCH_REF_REQUIRED
    message = (
        "Read requires search context first."
        if reason == ReasonCode.SEARCH_FIRST_REQUIRED
        else "Read requires candidate_id from latest search response."
    )
    gate_mode = _gate_mode()
    if gate_mode == "warn":
        return (True, None, [reason.value], [message])
    return (
        False,
        _stabilization_error(
            code=reason.value,
            message=message,
            reason_codes=[reason.value],
            warnings=[message],
            next_calls=_build_search_next_calls(target),
        ),
        [reason.value],
        [message],
    )


def _finalize_read_response(
    mode: str,
    args_map: Mapping[str, object],
    db: object,
    roots: list[str],
    response: ToolResult,
    *,
    warnings: list[str],
    suggested_next_action: str | None,
    budget_state: str,
    relevance_state: str,
    relevance_alternatives: list[str],
    reason_codes: list[str],
) -> ToolResult:
    if response.get("isError"):
        return response
    payload = _extract_json_payload(response)
    session_key = get_session_key(args_map, roots)
    if payload is not None:
        read_lines, read_chars, read_span = _derive_read_metrics(mode, payload)
    else:
        read_lines, read_chars, read_span = (0, 0, 0)
    metrics_snapshot = record_read_metrics(
        args_map,
        roots,
        read_lines=read_lines,
        read_chars=read_chars,
        read_span=read_span,
        db=db,
    )
    content_text = ""
    if payload is not None:
        if mode == "file":
            items = payload.get("content", [])
            if isinstance(items, list) and items and isinstance(items[0], Mapping):
                content_text = str(items[0].get("text", ""))
        elif mode == "symbol":
            content_text = str(payload.get("content", ""))
        elif mode == "snippet":
            parts: list[str] = []
            results = payload.get("results", [])
            if isinstance(results, list):
                for result in results:
                    if isinstance(result, Mapping):
                        parts.append(str(result.get("content", "")))
            content_text = "\n".join(parts)
        else:
            content_text = str(payload.get("diff", ""))

    bundle_meta = add_read_to_bundle(
        session_key,
        mode=mode,
        path=str(args_map.get("target") or args_map.get("path") or ""),
        text=content_text,
    )
    all_warnings = list(warnings)
    if relevance_state == "LOW_RELEVANCE":
        all_warnings.append("This target seems unrelated to recent search results.")
        reason_codes.append(ReasonCode.LOW_RELEVANCE_OUTSIDE_TOPK.value)
    extra = dict(bundle_meta)
    if relevance_alternatives:
        extra["alternatives"] = relevance_alternatives
    if relevance_state == "LOW_RELEVANCE":
        extra["relevance_code"] = "LOW_RELEVANCE"
    return _inject_stabilization(
        response,
        budget_state=budget_state,
        warnings=all_warnings,
        suggested_next_action=suggested_next_action,
        metrics_snapshot=metrics_snapshot,
        reason_codes=reason_codes,
        extra=extra,
    )


def execute_read(args: object, db: object, roots: list[str], logger: object = None) -> ToolResult:
    """Unified read entrypoint."""
    if not isinstance(args, Mapping):
        return invalid_args_response("read", "args must be an object")
    args_map = dict(args)
    if requires_strict_session_id(args_map):
        return _stabilization_error(
            code="STRICT_SESSION_ID_REQUIRED",
            message="session_id is required by strict session policy.",
            reason_codes=[],
            warnings=["Provide session_id or disable strict mode."],
            next_calls=[{"tool": "search", "arguments": {"query": "target"}}],
        )

    mode = str(args_map.get("mode") or "").strip()
    if mode not in _MODES:
        return mcp_response(
            "read",
            lambda: pack_error("read", ErrorCode.INVALID_ARGS, "'mode' must be one of: file, symbol, snippet, diff_preview"),
            lambda: {
                "error": {
                    "code": ErrorCode.INVALID_ARGS.value,
                    "message": "'mode' must be one of: file, symbol, snippet, diff_preview",
                },
                "isError": True,
            },
        )

    if "against" in args_map and mode != "diff_preview":
        return _invalid_mode_param("against", "diff_preview")
    if "against" in args_map:
        against = str(args_map.get("against") or "").strip()
        if against not in _DIFF_BASELINES:
            return mcp_response(
                "read",
                lambda: pack_error("read", ErrorCode.INVALID_ARGS, "'against' must be one of: HEAD, WORKTREE, INDEX"),
                lambda: {
                    "error": {
                        "code": ErrorCode.INVALID_ARGS.value,
                        "message": "'against' must be one of: HEAD, WORKTREE, INDEX",
                    },
                    "isError": True,
                },
            )

    for key in ("start_line", "end_line", "context_lines"):
        if key in args_map and mode != "snippet":
            return _invalid_mode_param(key, "snippet")

    for key in ("path", "include_context", "symbol_id", "sid", "name"):
        if key in args_map and mode != "symbol":
            return _invalid_mode_param(key, "symbol")

    target = str(args_map.get("target") or "").strip()
    delegated = dict(args_map)

    snapshot = get_metrics_snapshot(args_map, roots)
    budget_state, budget_warnings, budget_next = evaluate_budget_state(snapshot)
    if budget_state == "HARD_LIMIT":
        return _budget_exceeded_response()

    delegated, soft_degraded, soft_warnings = apply_soft_limits(mode, delegated)
    reason_codes: list[str] = []
    if soft_degraded:
        budget_state = "SOFT_LIMIT"
        reason_codes.append(ReasonCode.BUDGET_SOFT_LIMIT.value)
    all_budget_warnings = budget_warnings + soft_warnings

    search_ctx = get_search_context(args_map, roots)
    gate_ok, gate_error, gate_reasons, gate_warnings = _enforce_search_ref_gate(mode, delegated, search_ctx)
    reason_codes.extend(gate_reasons)
    all_budget_warnings.extend(gate_warnings)
    if not gate_ok and gate_error is not None:
        return gate_error
    relevance_state, relevance_warnings, relevance_alts, relevance_next = assess_relevance(mode, target, search_ctx)
    if relevance_warnings:
        all_budget_warnings.extend(relevance_warnings)
    next_action = relevance_next or budget_next

    if mode == "file":
        if target and "path" not in delegated:
            delegated["path"] = target
        response = execute_read_file(delegated, db, roots)
        return _finalize_read_response(
            mode,
            args_map,
            db,
            roots,
            response,
            warnings=all_budget_warnings,
            suggested_next_action=next_action,
            budget_state=budget_state,
            relevance_state=relevance_state,
            relevance_alternatives=relevance_alts,
            reason_codes=reason_codes,
        )

    if mode == "symbol":
        if target and not (delegated.get("name") or delegated.get("symbol_id") or delegated.get("sid")):
            delegated["name"] = target
        response = execute_read_symbol(delegated, db, logger, roots)
        return _finalize_read_response(
            mode,
            args_map,
            db,
            roots,
            response,
            warnings=all_budget_warnings,
            suggested_next_action=next_action,
            budget_state=budget_state,
            relevance_state=relevance_state,
            relevance_alternatives=relevance_alts,
            reason_codes=reason_codes,
        )

    if mode == "snippet":
        if target and not (delegated.get("tag") or delegated.get("query")):
            delegated["tag"] = target
        response = execute_get_snippet(delegated, db, roots)
        return _finalize_read_response(
            mode,
            args_map,
            db,
            roots,
            response,
            warnings=all_budget_warnings,
            suggested_next_action=next_action,
            budget_state=budget_state,
            relevance_state=relevance_state,
            relevance_alternatives=relevance_alts,
            reason_codes=reason_codes,
        )

    if target and "path" not in delegated:
        delegated["path"] = target
    response = execute_dry_run_diff(delegated, db, roots)
    return _finalize_read_response(
        mode,
        args_map,
        db,
        roots,
        response,
        warnings=all_budget_warnings,
        suggested_next_action=next_action,
        budget_state=budget_state,
        relevance_state=relevance_state,
        relevance_alternatives=relevance_alts,
        reason_codes=reason_codes,
    )
