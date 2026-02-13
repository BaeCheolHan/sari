from collections.abc import Mapping
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import TypeAlias

from sari.core.policy_engine import ReadPolicy, load_read_policy
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
    pack_encode_id,
    pack_encode_text,
    pack_error,
    resolve_db_path,
    resolve_fs_path,
    resolve_root_ids,
)
from sari.mcp.tools.crypto import issue_context_ref

ToolResult: TypeAlias = dict[str, object]

_MODES = {"file", "symbol", "snippet", "diff_preview", "ast_edit"}
_DIFF_BASELINES = {"HEAD", "WORKTREE", "INDEX"}
_SYMBOL_KIND_ENUM = ("function", "method", "class", "interface", "struct", "trait", "enum", "module")

_SYMBOL_KIND_ALIASES: dict[str, str] = {
    "func": "function",
    "fn": "function",
    "functions": "function",
    "methods": "method",
    "clazz": "class",
    "type": "class",
    "iface": "interface",
    "interfaces": "interface",
    "structures": "struct",
    "structs": "struct",
    "traits": "trait",
    "enums": "enum",
    "mod": "module",
    "mods": "module",
    "namespace": "module",
}

_AST_EDIT_ERROR_GUIDANCE: dict[str, tuple[str, str]] = {
    "VERSION_CONFLICT": ("re_read", "Re-run read to get latest version_hash and retry ast_edit."),
    "SYMBOL_KIND_INVALID": ("fix_args", "Use supported symbol_kind enum values."),
    "SYMBOL_RESOLUTION_FAILED": ("search_symbol", "Run search/read_symbol to refresh symbol target and hints."),
    "SYMBOL_NOT_FOUND": ("search_symbol", "Verify symbol name or pass symbol_qualname hint."),
    "SYMBOL_BLOCK_MISMATCH": ("adjust_old_text", "Set old_text from selected symbol block or omit old_text."),
    ErrorCode.INVALID_ARGS.value: ("fix_args", "Fix request arguments and retry."),
    ErrorCode.NOT_INDEXED.value: ("reindex", "Ensure target is inside workspace and indexed."),
    ErrorCode.IO_ERROR.value: ("retry", "Retry after checking file permission or transient IO state."),
}


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


def _compute_content_hash(text: str) -> str:
    return hashlib.sha1(str(text).encode("utf-8", "replace")).hexdigest()[:12]


def _mode_to_evidence_kind(mode: str) -> str:
    if mode == "diff_preview":
        return "diff"
    if mode in {"file", "symbol", "snippet"}:
        return mode
    return "file"


def _extract_file_text(payload: Mapping[str, object]) -> str:
    items = payload.get("content", [])
    if isinstance(items, list) and items and isinstance(items[0], Mapping):
        return str(items[0].get("text", ""))
    return str(payload.get("content", ""))


def _extract_evidence_refs(
    mode: str,
    payload: Mapping[str, object],
    delegated: Mapping[str, object],
    request: Mapping[str, object],
    bundle_id: str | None,
) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    kind = _mode_to_evidence_kind(mode)
    candidate_id = str(request.get("candidate_id") or "").strip()
    bundle = str(bundle_id or "").strip()

    def _add_common(ref: dict[str, object]) -> dict[str, object]:
        out = dict(ref)
        out["kind"] = kind
        if candidate_id:
            out.setdefault("candidate_id", candidate_id)
        if bundle:
            out.setdefault("bundle_id", bundle)
        return out

    if mode == "file":
        text = _extract_file_text(payload)
        path = str(request.get("path") or payload.get("path") or request.get("target") or "").strip()
        ref: dict[str, object] = {"path": path, "content_hash": _compute_content_hash(text)}

        req_start = request.get("start_line")
        req_end = request.get("end_line")
        if isinstance(req_start, int) and isinstance(req_end, int):
            ref["start_line"] = req_start
            ref["end_line"] = req_end
        else:
            offset = request.get("offset")
            if isinstance(offset, int) and offset >= 0:
                actual_lines = _line_count(text)
                ref["start_line"] = offset + 1
                ref["end_line"] = offset + actual_lines if actual_lines > 0 else offset
        refs.append(_add_common(ref))

    elif mode == "symbol":
        text = str(payload.get("content", ""))
        path = str(payload.get("path") or request.get("path") or request.get("target") or "").strip()
        ref = {
            "path": path,
            "content_hash": _compute_content_hash(text),
        }
        start_line = payload.get("start_line")
        end_line = payload.get("end_line")
        if isinstance(start_line, int):
            ref["start_line"] = start_line
        if isinstance(end_line, int):
            ref["end_line"] = end_line
        symbol = str(request.get("symbol") or request.get("name") or request.get("target") or payload.get("name") or "").strip()
        if symbol:
            ref["symbol"] = symbol
        refs.append(_add_common(ref))

    elif mode == "snippet":
        results = payload.get("results", [])
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, Mapping):
                    continue
                text = str(result.get("content") or result.get("text") or "")
                ref = {
                    "path": str(result.get("path") or request.get("path") or request.get("target") or "").strip(),
                    "content_hash": _compute_content_hash(text),
                }
                start_line = result.get("start_line")
                end_line = result.get("end_line")
                if isinstance(start_line, int):
                    ref["start_line"] = start_line
                if isinstance(end_line, int):
                    ref["end_line"] = end_line
                snippet_id = result.get("snippet_id")
                if snippet_id is None:
                    snippet_id = result.get("id")
                if snippet_id is not None:
                    ref["snippet_id"] = str(snippet_id)
                refs.append(_add_common(ref))

    else:
        text = str(payload.get("diff", ""))
        path = str(request.get("path") or payload.get("path") or request.get("target") or "").strip()
        against = str(request.get("against") or payload.get("against") or "").strip()
        ref = {
            "path": path,
            "content_hash": _compute_content_hash(text),
        }
        if against:
            ref["against"] = against
        refs.append(_add_common(ref))

    if refs:
        return refs

    # Success response must not have empty evidence.
    fallback_text = ""
    if mode == "file":
        fallback_text = _extract_file_text(payload)
    elif mode == "symbol":
        fallback_text = str(payload.get("content", ""))
    elif mode == "snippet":
        fallback_text = json.dumps(payload.get("results", []), ensure_ascii=False)
    else:
        fallback_text = str(payload.get("diff", ""))
    fallback_ref = _add_common(
        {
            "path": str(request.get("path") or request.get("target") or payload.get("path") or "").strip(),
            "content_hash": _compute_content_hash(fallback_text),
        }
    )
    return [fallback_ref]


def _attach_context_refs(evidence_refs: list[dict[str, object]], roots: list[str]) -> list[dict[str, object]]:
    if not evidence_refs:
        return evidence_refs
    root_ids = resolve_root_ids(roots)
    default_ws = root_ids[0] if root_ids else ""
    attached: list[dict[str, object]] = []
    for ref in evidence_refs:
        out = dict(ref)
        path = str(out.get("path") or "").strip()
        ws = default_ws
        for rid in root_ids:
            if path == rid or path.startswith(f"{rid}/"):
                ws = rid
                break
        payload = {
            "ws": ws,
            "kind": str(out.get("kind") or "file"),
            "path": path,
            "span": [int(out.get("start_line") or 0), int(out.get("end_line") or 0)],
            "ch": str(out.get("content_hash") or ""),
        }
        try:
            out["context_ref"] = issue_context_ref(payload)
        except Exception:
            pass
        attached.append(out)
    return attached


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


def _ast_edit_error(code: str, message: str) -> ToolResult:
    action, hint = _AST_EDIT_ERROR_GUIDANCE.get(code, ("fix_args", "Review request and retry."))
    return mcp_response(
        "read",
        lambda: pack_error("read", code, message),
        lambda: {
            "error": {
                "code": code,
                "message": message,
                "client_action": action,
                "hint": hint,
            },
            "meta": {
                "stabilization": {
                    "reason_codes": [code],
                    "warnings": [message],
                    "evidence_refs": [],
                    "suggested_next_action": action,
                }
            },
            "isError": True,
        },
    )


def _normalize_symbol_kind(value: str) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    return _SYMBOL_KIND_ALIASES.get(token, token)


def _build_change_preview(before: str, after: str, path: str, max_lines: int = 80) -> str:
    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{path}:before",
            tofile=f"{path}:after",
            lineterm="",
        )
    )
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"... ({len(diff) - max_lines} more lines)"]
    return "\n".join(diff)


def _build_edit_test_next_calls(target_fs_path: str, roots: list[str]) -> list[dict[str, object]]:
    fs_path = Path(target_fs_path)
    test_candidates: list[Path] = []
    if fs_path.name.endswith(".py"):
        stem = fs_path.stem
        test_candidates.extend(
            [
                fs_path.parent / f"test_{stem}.py",
                fs_path.parent.parent / "tests" / f"test_{stem}.py",
            ]
        )
        for root in roots:
            root_path = Path(root)
            if root_path.exists():
                test_candidates.append(root_path / "tests" / f"test_{stem}.py")
    for cand in test_candidates:
        if cand.exists():
            return [{"tool": "execute_shell_command", "arguments": {"command": f"pytest -q {cand}"}}]
    return [{"tool": "execute_shell_command", "arguments": {"command": "pytest -q"}}]


def _infer_symbol_name(args_map: Mapping[str, object], new_text: str) -> str:
    symbol = str(args_map.get("symbol") or "").strip()
    if symbol:
        return symbol
    text = str(new_text or "").strip()
    if not text:
        return ""
    try:
        module = ast.parse(text)
    except Exception:
        return ""
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return str(getattr(node, "name", "") or "").strip()
    return ""


def _build_symbol_test_next_calls(target_fs_path: str, roots: list[str], symbol: str) -> list[dict[str, object]]:
    if not symbol:
        return _build_edit_test_next_calls(target_fs_path, roots)
    found: list[Path] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        tests_dir = root_path / "tests"
        if not tests_dir.exists():
            continue
        for cand in tests_dir.rglob("test_*.py"):
            try:
                text = cand.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if symbol in text:
                found.append(cand)
            if len(found) >= 3:
                break
        if len(found) >= 3:
            break
    if not found:
        return _build_edit_test_next_calls(target_fs_path, roots)
    cmd = "pytest -q " + " ".join(str(p) for p in found)
    return [{"tool": "execute_shell_command", "arguments": {"command": cmd}}]


def _caller_paths_from_db(db: object, symbol: str, limit: int = 20) -> list[str]:
    if not symbol:
        return []
    conn = None
    if hasattr(db, "get_read_connection"):
        try:
            conn = db.get_read_connection()
        except Exception:
            conn = None
    if conn is None:
        conn = getattr(db, "_read", None)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT DISTINCT from_path FROM symbol_relations WHERE to_symbol = ? ORDER BY from_path LIMIT ?",
            (symbol, int(limit)),
        ).fetchall()
    except Exception:
        return []

    out: list[str] = []
    for row in rows:
        if isinstance(row, Mapping):
            out.append(str(row.get("from_path") or ""))
        elif isinstance(row, (list, tuple)) and row:
            out.append(str(row[0] or ""))
        else:
            try:
                out.append(str(row["from_path"]))
            except Exception:
                pass
    return [p for p in out if p]


def _candidate_test_paths_from_callers(db: object, roots: list[str], symbol: str) -> list[Path]:
    callers = _caller_paths_from_db(db, symbol, limit=40)
    if not callers:
        return []
    candidates: list[Path] = []
    for caller_db_path in callers:
        fs = resolve_fs_path(caller_db_path, roots)
        if not fs:
            continue
        p = Path(fs)
        if "tests" in p.parts and p.exists() and p.name.startswith("test_") and p.suffix == ".py":
            candidates.append(p)
    # Dedup while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out[:3]


def _python_symbol_span(source: str, symbol: str) -> tuple[int, int] | None:
    if not symbol:
        return None
    try:
        module = ast.parse(source)
    except Exception:
        return None
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and getattr(node, "name", "") == symbol:
            start = int(getattr(node, "lineno", 0) or 0)
            end = int(getattr(node, "end_lineno", 0) or 0)
            if start > 0 and end >= start:
                return (start, end)
    return None


def _js_like_symbol_span(source: str, symbol: str) -> tuple[int, int] | None:
    if not symbol:
        return None
    lines = source.splitlines()
    if not lines:
        return None
    patterns = [
        re.compile(rf"^\s*function\s+{re.escape(symbol)}\s*\("),
        re.compile(rf"^\s*(const|let|var)\s+{re.escape(symbol)}\s*=\s*(async\s*)?\("),
        re.compile(rf"^\s*(export\s+)?(async\s+)?function\s+{re.escape(symbol)}\s*\("),
        re.compile(rf"^\s*(export\s+)?(const|let|var)\s+{re.escape(symbol)}\s*=\s*"),
    ]
    start_idx = -1
    for i, line in enumerate(lines):
        if any(p.search(line) for p in patterns):
            start_idx = i
            break
    if start_idx < 0:
        return None

    depth = 0
    opened = False
    for j in range(start_idx, len(lines)):
        line = lines[j]
        depth += line.count("{")
        if line.count("{") > 0:
            opened = True
        depth -= line.count("}")
        if opened and depth <= 0:
            return (start_idx + 1, j + 1)
    return None


def _extract_tree_sitter_symbols(source: str, fs_path: str) -> list[object]:
    try:
        from sari.core.parsers.ast_engine import ASTEngine
        from sari.core.parsers.factory import ParserFactory
    except Exception:
        return []

    suffix = Path(fs_path).suffix.lower()
    language = ParserFactory.get_language(suffix)
    if not language:
        return []

    engine = ASTEngine()
    tree = engine.parse(language, source)
    if tree is None:
        return []
    try:
        parsed = engine.extract_symbols(fs_path, language, source, tree=tree)
    except Exception:
        return []
    return list(getattr(parsed, "symbols", []) or [])


def _normalize_symbol_token(value: str) -> str:
    return str(value or "").strip().replace("::", ".").replace("#", ".")


def _tree_sitter_symbol_span(
    source: str,
    fs_path: str,
    symbol: str,
    symbol_qualname: str = "",
    symbol_kind: str = "",
) -> tuple[int, int] | None:
    if not symbol:
        return None
    symbols = _extract_tree_sitter_symbols(source, fs_path)
    if not symbols:
        return None
    suffix = Path(fs_path).suffix.lower()
    raw_symbol = str(symbol or "").strip()
    normalized = _normalize_symbol_token(raw_symbol)
    target_name = normalized.split(".")[-1] if normalized else raw_symbol
    qualified_query = any(token in raw_symbol for token in (".", "::", "#"))
    qual_hint = _normalize_symbol_token(str(symbol_qualname or "").strip())
    kind_hint = str(symbol_kind or "").strip().lower()

    preferred_kinds: dict[str, tuple[str, ...]] = {
        ".java": ("method", "function"),
        ".kt": ("method", "function"),
        ".go": ("function", "method"),
        ".rs": ("function", "method"),
    }
    kinds = preferred_kinds.get(suffix, ("function", "method", "class"))

    best: tuple[int, int, int] | None = None
    for sym in symbols:
        name = str(getattr(sym, "name", "") or "").strip()
        qualname = str(getattr(sym, "qualname", "") or "").strip()
        norm_qual = _normalize_symbol_token(qualname)
        start = int(getattr(sym, "line", 0) or 0)
        end = int(getattr(sym, "end_line", 0) or 0)
        if start <= 0 or end < start:
            continue
        kind = str(getattr(sym, "kind", "") or "").strip().lower()

        score = 100
        if qual_hint and norm_qual and norm_qual == qual_hint:
            score = -2
        elif qualname and (qualname == raw_symbol or norm_qual == normalized):
            score = 0
        elif qualified_query and norm_qual and norm_qual.endswith(f".{target_name}"):
            score = 2
        elif name == raw_symbol:
            score = 4
        elif name == target_name:
            score = 6
        else:
            continue

        try:
            kind_penalty = kinds.index(kind)
        except ValueError:
            kind_penalty = len(kinds) + 1
        if kind_hint:
            if kind == kind_hint:
                kind_penalty -= 1
            else:
                kind_penalty += len(kinds) + 2
        candidate = (score, kind_penalty, start)
        if best is None or candidate < best:
            best = candidate
            best_span = (start, end)
    if best is not None:
        return best_span
    return None


def _replace_line_span(source: str, start_line: int, end_line: int, new_block: str) -> str:
    lines = source.splitlines(keepends=True)
    if start_line <= 0 or end_line < start_line or end_line > len(lines):
        raise ValueError("invalid replacement span")
    block = str(new_block or "")
    if block and not block.endswith("\n"):
        block += "\n"
    replacement_lines = block.splitlines(keepends=True)
    out = lines[: start_line - 1] + replacement_lines + lines[end_line:]
    return "".join(out)


def _wait_focus_sync(indexer: object, timeout_ms: int) -> tuple[str, str]:
    getter = getattr(indexer, "get_queue_depths", None)
    if not callable(getter):
        return ("unsupported", "focus sync check unsupported: no get_queue_depths")
    deadline = time.time() + max(1, int(timeout_ms)) / 1000.0
    while time.time() < deadline:
        try:
            depths_raw = getter()
        except Exception:
            return ("failed", "focus sync check failed while reading queue depths")
        depths = depths_raw if isinstance(depths_raw, Mapping) else {}
        fair = int(depths.get("fair_queue", 0) or 0)
        priority = int(depths.get("priority_queue", 0) or 0)
        writer = int(depths.get("db_writer", 0) or 0)
        if fair == 0 and priority == 0 and writer == 0:
            return ("complete", "")
        time.sleep(0.05)
    return ("timeout", "focus sync timeout; indexing queue still busy")


def _execute_ast_edit(args_map: Mapping[str, object], db: object, roots: list[str]) -> ToolResult:
    target = str(args_map.get("target") or "").strip()
    expected_hash = str(args_map.get("expected_version_hash") or "").strip()
    old_text = str(args_map.get("old_text") or "")
    new_text = str(args_map.get("new_text") or "")
    symbol = str(args_map.get("symbol") or "").strip()
    symbol_qualname = str(args_map.get("symbol_qualname") or "").strip()
    symbol_kind_raw = str(args_map.get("symbol_kind") or "").strip()
    symbol_kind = _normalize_symbol_kind(symbol_kind_raw)
    preview = bool(args_map.get("ast_edit_preview", False))
    sync_timeout_ms = int(args_map.get("sync_timeout_ms") or 500)
    if not target:
        return _ast_edit_error(ErrorCode.INVALID_ARGS.value, "target is required for mode=ast_edit")
    if not expected_hash:
        return _ast_edit_error(ErrorCode.INVALID_ARGS.value, "expected_version_hash is required for mode=ast_edit")
    if old_text == "" and not symbol:
        return _ast_edit_error(ErrorCode.INVALID_ARGS.value, "old_text is required for mode=ast_edit")
    if symbol_kind and symbol_kind not in _SYMBOL_KIND_ENUM:
        allowed = ", ".join(_SYMBOL_KIND_ENUM)
        return _ast_edit_error("SYMBOL_KIND_INVALID", f"symbol_kind must be one of: {allowed}")

    db_path = resolve_db_path(target, roots, db=db)
    if not db_path:
        return _ast_edit_error(ErrorCode.NOT_INDEXED.value, "target is not indexed or out of workspace scope")
    fs_path = resolve_fs_path(db_path, roots)
    if not fs_path:
        return _ast_edit_error(ErrorCode.NOT_INDEXED.value, "unable to resolve target filesystem path")
    path_obj = Path(fs_path)
    if not path_obj.exists():
        return _ast_edit_error(ErrorCode.NOT_INDEXED.value, "target file does not exist")

    try:
        original = path_obj.read_text(encoding="utf-8")
    except Exception as exc:
        return _ast_edit_error(ErrorCode.IO_ERROR.value, f"failed to read target: {exc}")

    current_hash = _compute_content_hash(original)
    if current_hash != expected_hash:
        return _ast_edit_error("VERSION_CONFLICT", "version_hash mismatch; re-read target before editing")
    edited = ""
    if symbol:
        suffix = path_obj.suffix.lower()
        span = None
        if suffix == ".py":
            span = _python_symbol_span(original, symbol)
        elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
            span = _js_like_symbol_span(original, symbol)
        else:
            span = _tree_sitter_symbol_span(
                original,
                str(path_obj),
                symbol,
                symbol_qualname=symbol_qualname,
                symbol_kind=symbol_kind,
            )
            if not span:
                return _ast_edit_error(
                    "SYMBOL_RESOLUTION_FAILED",
                    f"symbol-based ast_edit could not resolve symbol '{symbol}' in {suffix or 'target'} "
                    "(tree-sitter parser/runtime unavailable or symbol missing)",
                )
        if not span:
            return _ast_edit_error("SYMBOL_NOT_FOUND", f"symbol '{symbol}' was not found in target")
        start_line, end_line = span
        if old_text:
            selected = "".join(original.splitlines(keepends=True)[start_line - 1:end_line])
            if old_text not in selected:
                return _ast_edit_error("SYMBOL_BLOCK_MISMATCH", "old_text was not found in selected symbol block")
        try:
            edited = _replace_line_span(original, start_line, end_line, new_text)
        except ValueError as exc:
            return _ast_edit_error(ErrorCode.INVALID_ARGS.value, str(exc))
    else:
        if old_text not in original:
            return _ast_edit_error(ErrorCode.INVALID_ARGS.value, "old_text was not found in target")
        edited = original.replace(old_text, new_text, 1)
    if str(path_obj).endswith(".py"):
        try:
            ast.parse(edited)
        except Exception as exc:
            return _ast_edit_error(ErrorCode.INVALID_ARGS.value, f"edited python source is invalid syntax: {exc}")
    new_hash = _compute_content_hash(edited)
    if preview:
        change_preview = _build_change_preview(original, edited, db_path)
        return mcp_response(
            "read",
            lambda: "\n".join(
                [
                    f"PACK1 tool=read ok=true mode=ast_edit path={db_path} preview=true returned=1",
                    f"m:next_call=apply read(ast_edit) with expected_version_hash={current_hash}",
                ]
            ),
            lambda: {
                "mode": "ast_edit",
                "path": db_path,
                "preview": True,
                "updated": False,
                "focus_indexing": "skipped",
                "focus_sync_state": "not_requested",
                "previous_version_hash": current_hash,
                "version_hash": current_hash,
                "preview_version_hash": new_hash,
                "change_preview": change_preview,
                "meta": {
                    "stabilization": {
                        "reason_codes": ["AST_EDIT_PREVIEW"],
                        "warnings": [],
                        "evidence_refs": [],
                        "suggested_next_action": "read",
                        "next_calls": [
                            {
                                "tool": "read",
                                "arguments": {
                                    "mode": "ast_edit",
                                    "target": target,
                                    "expected_version_hash": current_hash,
                                    "old_text": old_text,
                                    "new_text": new_text,
                                    "symbol": symbol,
                                    "symbol_qualname": symbol_qualname,
                                    "symbol_kind": symbol_kind,
                                },
                            }
                        ],
                    }
                },
            },
        )

    try:
        path_obj.write_text(edited, encoding="utf-8")
    except Exception as exc:
        return _ast_edit_error(ErrorCode.IO_ERROR.value, f"failed to write target: {exc}")

    focus_indexing = "deferred"
    focus_sync_state = "not_requested"
    warnings: list[str] = []
    indexer = args_map.get("__indexer__")
    if indexer is not None:
        try:
            if hasattr(indexer, "index_file"):
                res = indexer.index_file(str(path_obj))
                if isinstance(res, Mapping) and not bool(res.get("ok", True)):
                    focus_indexing = "failed"
                    focus_sync_state = "skipped"
                    warnings.append(str(res.get("message") or "focus indexing failed"))
                else:
                    focus_indexing = "triggered"
                    focus_sync_state, sync_warning = _wait_focus_sync(indexer, sync_timeout_ms)
                    if sync_warning:
                        warnings.append(sync_warning)
            elif hasattr(indexer, "request_reindex"):
                indexer.request_reindex(str(path_obj))
                focus_indexing = "triggered"
                focus_sync_state, sync_warning = _wait_focus_sync(indexer, sync_timeout_ms)
                if sync_warning:
                    warnings.append(sync_warning)
            else:
                focus_indexing = "deferred"
                focus_sync_state = "unsupported"
                warnings.append("focus indexing deferred: indexer has no index_file/request_reindex")
        except Exception:
            focus_indexing = "failed"
            focus_sync_state = "failed"
            warnings.append("focus indexing failed due to indexer exception")
    else:
        warnings.append("focus indexing deferred: indexer unavailable")
    symbol_for_tests = _infer_symbol_name(args_map, new_text) or symbol
    caller_tests = _candidate_test_paths_from_callers(db, roots, symbol_for_tests)
    if caller_tests:
        next_calls = [
            {
                "tool": "execute_shell_command",
                "arguments": {"command": "pytest -q " + " ".join(str(p) for p in caller_tests)},
            }
        ]
    else:
        next_calls = _build_symbol_test_next_calls(str(path_obj), roots, symbol_for_tests)
    return mcp_response(
        "read",
        lambda: "\n".join(
            [
                f"PACK1 tool=read ok=true mode=ast_edit path={db_path} version_hash={new_hash} returned=1",
                f"m:next_call={next_calls[0]['arguments']['command']}",
            ]
        ),
        lambda: {
            "mode": "ast_edit",
            "path": db_path,
            "updated": True,
            "focus_indexing": focus_indexing,
            "focus_sync_state": focus_sync_state,
            "previous_version_hash": current_hash,
            "version_hash": new_hash,
            "meta": {
                "stabilization": {
                    "reason_codes": ["AST_EDIT_APPLIED"],
                    "warnings": warnings,
                    "evidence_refs": [],
                    "suggested_next_action": "execute_shell_command",
                    "next_calls": next_calls,
                }
            },
        },
    )


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
    evidence_refs: list[dict[str, object]] | None = None,
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
    stabilization_dict["evidence_refs"] = list(evidence_refs or [])
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


def _inject_pack_next_hint(
    response: ToolResult,
    *,
    mode: str,
    args_map: Mapping[str, object],
) -> ToolResult:
    if mode != "symbol":
        return response
    content = response.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], Mapping):
        return response
    text = str(content[0].get("text") or "")
    if not text.startswith("PACK1 "):
        return response
    if "\nSARI_NEXT:" in text:
        return response
    symbol = str(args_map.get("target") or args_map.get("name") or "").strip()
    if not symbol:
        return response
    path = str(args_map.get("path") or "").strip()
    args = [f"name={pack_encode_text(symbol)}"]
    if path:
        args.append(f"path={pack_encode_id(path)}")
    next_line = f"SARI_NEXT: get_callers({','.join(args)})"
    content[0]["text"] = f"{text.rstrip()}\n{next_line}"
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
                    "evidence_refs": [],
                    "next_calls": [{"tool": "search", "arguments": {"query": "target", "search_type": "code", "limit": 5}}],
                }
            },
            "isError": True,
        },
    )


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
                    "evidence_refs": [],
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
    policy: ReadPolicy,
) -> tuple[bool, ToolResult | None, list[str], list[str]]:
    if mode == "snippet":
        search_count = int(search_context.get("search_count", 0) or 0)
        if search_count > 0:
            return (True, None, [], [])
        reason = ReasonCode.SEARCH_FIRST_REQUIRED
        message = "Snippet read requires search context first."
        if policy.gate_mode == "warn":
            return (True, None, [reason.value], [message])
        return (
            False,
            _stabilization_error(
                code=reason.value,
                message=message,
                reason_codes=[reason.value],
                warnings=[message],
                next_calls=[{"tool": "search", "arguments": {"query": "snippet", "search_type": "code", "limit": 5}}],
            ),
            [reason.value],
            [message],
        )
    max_lines = int(policy.max_range_lines)
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
    if policy.gate_mode == "warn":
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
    delegated: Mapping[str, object],
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
    if payload is None:
        return _inject_pack_next_hint(response, mode=mode, args_map=args_map)
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
    evidence_refs = _extract_evidence_refs(
        mode,
        payload if payload is not None else {},
        delegated,
        args_map,
        str(bundle_meta.get("context_bundle_id") or ""),
    )
    evidence_refs = _attach_context_refs(evidence_refs, roots)
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
        evidence_refs=evidence_refs,
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
            reason_codes=["STRICT_SESSION_ID_REQUIRED"],
            warnings=["Provide session_id or disable strict mode."],
            next_calls=[{"tool": "search", "arguments": {"query": "target"}}],
        )

    mode = str(args_map.get("mode") or "").strip()
    if mode not in _MODES:
        return mcp_response(
            "read",
            lambda: pack_error("read", ErrorCode.INVALID_ARGS, "'mode' must be one of: file, symbol, snippet, diff_preview, ast_edit"),
            lambda: {
                "error": {
                    "code": ErrorCode.INVALID_ARGS.value,
                    "message": "'mode' must be one of: file, symbol, snippet, diff_preview, ast_edit",
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

    if mode == "ast_edit":
        return _execute_ast_edit(args_map, db, roots)

    for key in ("start_line", "end_line", "context_lines"):
        if key in args_map and mode != "snippet":
            return _invalid_mode_param(key, "snippet")

    for key in ("path", "include_context", "symbol_id", "sid", "name"):
        if key in args_map and mode != "symbol":
            return _invalid_mode_param(key, "symbol")

    target = str(args_map.get("target") or "").strip()
    delegated = dict(args_map)
    read_policy = load_read_policy()

    snapshot = get_metrics_snapshot(args_map, roots)
    budget_state, budget_warnings, budget_next = evaluate_budget_state(snapshot, policy=read_policy)
    if budget_state == "HARD_LIMIT":
        return _budget_exceeded_response()

    delegated, soft_degraded, soft_warnings = apply_soft_limits(mode, delegated, policy=read_policy)
    reason_codes: list[str] = []
    if soft_degraded:
        budget_state = "SOFT_LIMIT"
        reason_codes.append(ReasonCode.BUDGET_SOFT_LIMIT.value)
    all_budget_warnings = budget_warnings + soft_warnings

    search_ctx = get_search_context(args_map, roots)
    gate_ok, gate_error, gate_reasons, gate_warnings = _enforce_search_ref_gate(
        mode,
        delegated,
        search_ctx,
        read_policy,
    )
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
            delegated,
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
            delegated,
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
        if "max_results" in delegated and "limit" not in delegated:
            delegated["limit"] = delegated.get("max_results")
        response = execute_get_snippet(delegated, db, roots)
        snippet_payload = _extract_json_payload(response)
        if snippet_payload is not None:
            results = snippet_payload.get("results", [])
            if isinstance(results, list) and len(results) == 0:
                msg = "No snippet results for requested target."
                return _stabilization_error(
                    code="NO_RESULTS",
                    message=msg,
                    reason_codes=["NO_RESULTS"],
                    warnings=[msg],
                    next_calls=_build_search_next_calls(str(target or delegated.get("tag") or "snippet")),
                )
        return _finalize_read_response(
            mode,
            args_map,
            delegated,
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
        delegated,
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
