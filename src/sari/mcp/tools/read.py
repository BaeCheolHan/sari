from collections.abc import Mapping
import json
import os
from typing import TypeAlias

from sari.mcp.stabilization.aggregation import add_read_to_bundle
from sari.mcp.stabilization.budget_guard import apply_soft_limits, evaluate_budget_state
from sari.mcp.stabilization.relevance_guard import assess_relevance
from sari.mcp.stabilization.session_state import (
    get_metrics_snapshot,
    get_search_context,
    get_session_key,
    record_read_metrics,
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
            "isError": True,
        },
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
        extra=extra,
    )


def execute_read(args: object, db: object, roots: list[str], logger: object = None) -> ToolResult:
    """Unified read entrypoint."""
    if not isinstance(args, Mapping):
        return invalid_args_response("read", "args must be an object")
    args_map = dict(args)

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
    if soft_degraded:
        budget_state = "SOFT_LIMIT"
    all_budget_warnings = budget_warnings + soft_warnings

    search_ctx = get_search_context(args_map, roots)
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
    )
