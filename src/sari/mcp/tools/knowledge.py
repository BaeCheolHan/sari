import hashlib
import json
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeAlias

from sari.mcp.tools._util import (
    ErrorCode,
    invalid_args_response,
    mcp_response,
    pack_encode_id,
    pack_encode_text,
    pack_error,
    pack_header,
    pack_line,
    require_db_schema,
    resolve_fs_path,
    resolve_root_ids,
)
from sari.mcp.tools.crypto import verify_context_ref

ToolResult: TypeAlias = dict[str, object]


def _bool_env(name: str) -> bool:
    import os

    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []


def _coerce_limit(value: object, default: int = 20) -> int:
    try:
        n = int(value if value is not None else default)
    except Exception:
        n = default
    return max(1, min(n, 200))


def _json_error(code: ErrorCode, message: str) -> ToolResult:
    return {
        "error": {"code": code.value, "message": message},
        "isError": True,
    }


def _split_path(path: str, ws: str) -> tuple[str, str]:
    if path.startswith(f"{ws}/"):
        rel = path[len(ws) + 1 :]
        return ws, rel
    if "/" in path:
        root_id, rel = path.split("/", 1)
        return root_id, rel
    return ws, path


def _validate_ref_scope(payload: Mapping[str, object], roots: list[str]) -> None:
    ws = str(payload.get("ws") or "").strip()
    if not ws:
        raise ValueError("context_ref is missing ws")
    allowed = set(resolve_root_ids(roots))
    if allowed and ws not in allowed:
        raise ValueError("context_ref ws is out of scope")


def _save_context(
    db: object,
    *,
    key: str,
    content: str,
    labels: list[str],
    metadata: Mapping[str, object],
    context_payload: Mapping[str, object],
) -> object:
    tags = labels or _string_list(metadata.get("tags") if isinstance(metadata, Mapping) else None)
    related_files = _string_list(metadata.get("related_files") if isinstance(metadata, Mapping) else None)
    path = str(context_payload.get("path") or "").strip()
    if path and path not in related_files:
        related_files.append(path)
    data = {
        "topic": key,
        "content": content,
        "tags": tags,
        "related_files": related_files,
        "source": str((metadata or {}).get("source") or "knowledge.save"),
        "valid_from": 0,
        "valid_until": 0,
        "deprecated": False,
    }
    return db.contexts.upsert(data)


def _save_snippet(
    db: object,
    *,
    key: str,
    content: str,
    metadata: Mapping[str, object],
    context_payload: Mapping[str, object],
) -> dict[str, object]:
    ws = str(context_payload.get("ws") or "")
    path = str(context_payload.get("path") or "").strip()
    span = context_payload.get("span")
    if not path:
        raise ValueError("context_ref is missing path")
    if not isinstance(span, Sequence) or len(span) < 2:
        raise ValueError("context_ref is missing span")
    start_line = int(span[0])
    end_line = int(span[1])
    if start_line <= 0 or end_line < start_line:
        raise ValueError("context_ref span is invalid")

    root_id, rel = _split_path(path, ws)
    repo = rel.split("/", 1)[0] if rel else "__root__"
    now = int(time.time())
    note = str((metadata or {}).get("note") or "")
    commit_hash = str((metadata or {}).get("commit") or "")
    content_hash = hashlib.sha1(content.encode("utf-8", "replace")).hexdigest()
    row = (
        key,
        path,
        start_line,
        end_line,
        content,
        content_hash,
        "",
        "",
        repo or "__root__",
        root_id,
        note,
        commit_hash,
        now,
        now,
        "{}",
    )
    prev = getattr(db, "_writer_thread_id", None)
    db.register_writer_thread(threading.get_ident())
    try:
        with db._lock:
            cur = db._write.cursor()
            db.upsert_snippet_tx(cur, [row])
            db._write.commit()
    finally:
        db.register_writer_thread(prev)

    return {
        "tag": key,
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "hash": content_hash,
    }


def _execute_save(args: Mapping[str, object], db: object, roots: list[str]) -> ToolResult:
    context_ref = str(args.get("context_ref") or "").strip()
    type_name = str(args.get("type") or "").strip().lower()
    key = str(args.get("key") or "").strip()
    content = str(args.get("content") or "")
    metadata = args.get("metadata")
    labels = _string_list(args.get("labels"))
    if not isinstance(metadata, Mapping):
        metadata = {}

    if not context_ref:
        return _json_error(ErrorCode.INVALID_ARGS, "context_ref is required")
    if not type_name:
        return _json_error(ErrorCode.INVALID_ARGS, "type is required")
    if type_name not in {"context", "snippet"}:
        return _json_error(ErrorCode.INVALID_ARGS, "type must be one of: context, snippet")
    if not key:
        return _json_error(ErrorCode.INVALID_ARGS, "key is required")
    if not content.strip():
        return _json_error(ErrorCode.INVALID_ARGS, "content is required")

    try:
        decoded = verify_context_ref(context_ref)
        _validate_ref_scope(decoded, roots)
    except ValueError as exc:
        return _json_error(ErrorCode.INVALID_ARGS, str(exc))

    expected_hash = str(decoded.get("ch") or "").strip()
    actual_hash = hashlib.sha1(content.encode("utf-8", "replace")).hexdigest()[:12]
    if expected_hash and expected_hash != actual_hash:
        return _json_error(ErrorCode.INVALID_ARGS, "content hash mismatch with context_ref")

    if type_name == "context":
        saved = _save_context(
            db,
            key=key,
            content=content,
            labels=labels,
            metadata=metadata,
            context_payload=decoded,
        )
        if hasattr(saved, "model_dump"):
            saved_payload = saved.model_dump()
        elif isinstance(saved, Mapping):
            saved_payload = dict(saved)
        else:
            saved_payload = {"topic": key, "content": content}
    else:
        saved_payload = _save_snippet(
            db,
            key=key,
            content=content,
            metadata=metadata,
            context_payload=decoded,
        )

    return {
        "action": "save",
        "type": type_name,
        "saved": saved_payload,
        "warnings": [],
    }


def _execute_recall(args: Mapping[str, object], db: object, roots: list[str], alias_used: bool) -> ToolResult:
    type_name = str(args.get("type") or "").strip().lower()
    key = str(args.get("key") or args.get("topic") or args.get("tag") or "").strip()
    query = str(args.get("query") or "").strip()
    limit = _coerce_limit(args.get("limit"), 20)
    warnings: list[str] = []
    if alias_used:
        warnings.append("ACTION_ALIAS_DEPRECATED: use recall")

    if type_name not in {"context", "snippet"}:
        return _json_error(ErrorCode.INVALID_ARGS, "type must be one of: context, snippet")

    include_orphaned = _parse_include_orphaned(args)
    scope_root_ids, scope_err = _resolve_scope_root_ids(args, roots)
    if scope_err:
        return _json_error(ErrorCode.INVALID_ARGS, scope_err)

    if type_name == "context":
        guard = require_db_schema(db, "knowledge", "contexts", ["topic", "content"])
        if guard:
            return _extract_json_from_response(guard)
        if key:
            row = db.contexts.get_context_by_topic(key, as_of=0)
            rows = [row] if row else []
        elif query:
            rows = db.contexts.search_contexts(query, limit=limit, as_of=0)
        else:
            return _json_error(ErrorCode.INVALID_ARGS, "key or query is required for recall")
        results = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rows]
        results = _apply_context_scope_and_orphan_filter(results, scope_root_ids, roots, include_orphaned)
    else:
        guard = require_db_schema(db, "knowledge", "snippets", ["tag", "path", "content"])
        if guard:
            return _extract_json_from_response(guard)
        if key:
            try:
                rows = db.list_snippets_by_tag(key, limit=limit)
            except Exception:
                rows = db.list_snippets_by_tag(key)[:limit]
        elif query:
            rows = db.search_snippets(query, limit=limit)
        else:
            return _json_error(ErrorCode.INVALID_ARGS, "key or query is required for recall")
        norm = []
        for row in rows:
            if hasattr(row, "model_dump"):
                norm.append(row.model_dump())
            elif isinstance(row, Mapping):
                norm.append(dict(row))
            else:
                norm.append({})
        results = _apply_snippet_scope_and_orphan_filter(norm, scope_root_ids, roots, include_orphaned)

    return {
        "action": "recall",
        "type": type_name,
        "query": query,
        "key": key,
        "results": results,
        "count": len(results),
        "warnings": warnings,
    }


def _parse_memory_ref(memory_ref: str) -> tuple[str, str]:
    text = str(memory_ref or "").strip()
    if ":" not in text:
        return "", ""
    kind, ident = text.split(":", 1)
    return kind.strip().lower(), ident.strip()


def _parse_include_orphaned(args: Mapping[str, object]) -> bool:
    raw = args.get("include_orphaned")
    if raw is None and isinstance(args.get("options"), Mapping):
        raw = args["options"].get("include_orphaned")
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_scope_root_ids(args: Mapping[str, object], roots: list[str]) -> tuple[set[str], str | None]:
    allowed = set(resolve_root_ids(roots))
    options = args.get("options")
    options_map = options if isinstance(options, Mapping) else {}
    scope = str(options_map.get("scope") or "local").strip().lower()
    if scope not in {"local", "cross"}:
        return set(), "options.scope must be one of: local, cross"
    if scope == "local":
        return allowed, None

    refs = _string_list(options_map.get("workspace_refs"))
    if not refs:
        return set(), "options.workspace_refs is required when options.scope='cross'"
    requested = set(refs)
    if allowed and not requested.issubset(allowed):
        return set(), "options.workspace_refs includes out-of-scope root_id"
    return requested, None


def _is_orphaned_path(path: str, roots: list[str]) -> bool:
    db_path = str(path or "").strip()
    if not db_path:
        return True
    fs_path = resolve_fs_path(db_path, roots)
    if not fs_path:
        return True
    return not Path(fs_path).exists()


def _apply_snippet_scope_and_orphan_filter(
    rows: list[dict[str, object]],
    scope_root_ids: set[str],
    roots: list[str],
    include_orphaned: bool,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        root_id = str(item.get("root_id") or "")
        if scope_root_ids and root_id and root_id not in scope_root_ids:
            continue
        orphaned = _is_orphaned_path(str(item.get("path") or ""), roots)
        item["orphaned"] = orphaned
        if orphaned and not include_orphaned:
            continue
        out.append(item)
    return out


def _apply_context_scope_and_orphan_filter(
    rows: list[dict[str, object]],
    scope_root_ids: set[str],
    roots: list[str],
    include_orphaned: bool,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        related = _string_list(item.get("related_files"))
        if scope_root_ids and related:
            in_scope = False
            for path in related:
                for rid in scope_root_ids:
                    if path == rid or path.startswith(f"{rid}/"):
                        in_scope = True
                        break
                if in_scope:
                    break
            if not in_scope:
                continue
        orphaned = True
        if related:
            orphaned = all(_is_orphaned_path(path, roots) for path in related)
        item["orphaned"] = orphaned
        if orphaned and not include_orphaned:
            continue
        out.append(item)
    return out


def _execute_list(args: Mapping[str, object], db: object, roots: list[str]) -> ToolResult:
    type_name = str(args.get("type") or "").strip().lower()
    limit = _coerce_limit(args.get("limit"), 20)
    include_orphaned = _parse_include_orphaned(args)
    scope_root_ids, scope_err = _resolve_scope_root_ids(args, roots)
    if scope_err:
        return _json_error(ErrorCode.INVALID_ARGS, scope_err)
    if type_name not in {"context", "snippet"}:
        return _json_error(ErrorCode.INVALID_ARGS, "type must be one of: context, snippet")

    conn = db.get_read_connection() if hasattr(db, "get_read_connection") else getattr(db, "_read", None)
    if conn is None:
        return _json_error(ErrorCode.DB_ERROR, "db read connection is unavailable")

    if type_name == "context":
        rows = conn.execute(
            """
            SELECT topic, content, deprecated, updated_ts
            FROM contexts
            ORDER BY updated_ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        rows_norm = [
            {
                "memory_ref": f"context:{str(r['topic']) if isinstance(r, Mapping) else str(r[0])}",
                "topic": str(r["topic"]) if isinstance(r, Mapping) else str(r[0]),
                "content": str(r["content"]) if isinstance(r, Mapping) else str(r[1]),
                "deprecated": bool(int(r["deprecated"])) if isinstance(r, Mapping) else bool(int(r[2])),
                "updated_ts": int(r["updated_ts"]) if isinstance(r, Mapping) else int(r[3]),
            }
            for r in rows
        ]
        results = _apply_context_scope_and_orphan_filter(rows_norm, scope_root_ids, roots, include_orphaned)
    else:
        rows = conn.execute(
            """
            SELECT id, tag, path, start_line, end_line, updated_ts
            FROM snippets
            ORDER BY updated_ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        rows_norm = [
            {
                "memory_ref": f"snippet:{int(r['id']) if isinstance(r, Mapping) else int(r[0])}",
                "id": int(r["id"]) if isinstance(r, Mapping) else int(r[0]),
                "tag": str(r["tag"]) if isinstance(r, Mapping) else str(r[1]),
                "path": str(r["path"]) if isinstance(r, Mapping) else str(r[2]),
                "start_line": int(r["start_line"]) if isinstance(r, Mapping) else int(r[3]),
                "end_line": int(r["end_line"]) if isinstance(r, Mapping) else int(r[4]),
                "updated_ts": int(r["updated_ts"]) if isinstance(r, Mapping) else int(r[5]),
            }
            for r in rows
        ]
        results = _apply_snippet_scope_and_orphan_filter(rows_norm, scope_root_ids, roots, include_orphaned)

    return {
        "action": "list",
        "type": type_name,
        "results": results,
        "count": len(results),
        "warnings": [],
    }


def _execute_delete(args: Mapping[str, object], db: object) -> ToolResult:
    type_name = str(args.get("type") or "").strip().lower()
    memory_ref = str(args.get("memory_ref") or "").strip()
    key = str(args.get("key") or "").strip()
    if not memory_ref and not key:
        return _json_error(ErrorCode.INVALID_ARGS, "memory_ref or key is required")
    if type_name not in {"context", "snippet"}:
        return _json_error(ErrorCode.INVALID_ARGS, "type must be one of: context, snippet")

    now = int(time.time())
    conn = db._write if hasattr(db, "_write") else db.get_connection()
    cur = conn.cursor()

    if type_name == "context":
        topic = key
        if memory_ref:
            kind, ident = _parse_memory_ref(memory_ref)
            if kind and kind != "context":
                return _json_error(ErrorCode.INVALID_ARGS, "memory_ref kind/type mismatch")
            topic = ident or topic
        if not topic:
            return _json_error(ErrorCode.INVALID_ARGS, "context topic is required")
        cur.execute("UPDATE contexts SET deprecated = 1, updated_ts = ? WHERE topic = ?", (now, topic))
        conn.commit()
        deleted = int(cur.rowcount or 0)
        return {
            "action": "delete",
            "type": "context",
            "deleted": deleted,
            "memory_ref": f"context:{topic}",
            "warnings": [],
        }

    snippet_id = 0
    if memory_ref:
        kind, ident = _parse_memory_ref(memory_ref)
        if kind and kind != "snippet":
            return _json_error(ErrorCode.INVALID_ARGS, "memory_ref kind/type mismatch")
        try:
            snippet_id = int(ident or "0")
        except Exception:
            return _json_error(ErrorCode.INVALID_ARGS, "snippet memory_ref must contain numeric id")
    if snippet_id <= 0:
        return _json_error(ErrorCode.INVALID_ARGS, "snippet delete requires memory_ref=snippet:<id>")

    cur.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))
    conn.commit()
    deleted = int(cur.rowcount or 0)
    return {
        "action": "delete",
        "type": "snippet",
        "deleted": deleted,
        "memory_ref": f"snippet:{snippet_id}",
        "warnings": [],
    }


def _execute_relink(args: Mapping[str, object], db: object, roots: list[str]) -> ToolResult:
    type_name = str(args.get("type") or "").strip().lower()
    memory_ref = str(args.get("memory_ref") or "").strip()
    new_context_ref = str(args.get("new_context_ref") or "").strip()
    if not memory_ref or not new_context_ref:
        return _json_error(ErrorCode.INVALID_ARGS, "memory_ref and new_context_ref are required")
    confirm = str(args.get("confirm") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not confirm:
        return _json_error(ErrorCode.INVALID_ARGS, "relink requires confirm=true")
    if type_name not in {"context", "snippet"}:
        return _json_error(ErrorCode.INVALID_ARGS, "type must be one of: context, snippet")
    try:
        decoded = verify_context_ref(new_context_ref)
        _validate_ref_scope(decoded, roots)
    except ValueError as exc:
        return _json_error(ErrorCode.INVALID_ARGS, str(exc))

    now = int(time.time())
    conn = db._write if hasattr(db, "_write") else db.get_connection()
    cur = conn.cursor()

    kind, ident = _parse_memory_ref(memory_ref)
    if type_name == "context":
        if kind and kind != "context":
            return _json_error(ErrorCode.INVALID_ARGS, "memory_ref kind/type mismatch")
        topic = ident
        if not topic:
            return _json_error(ErrorCode.INVALID_ARGS, "context relink requires memory_ref=context:<topic>")
        path = str(decoded.get("path") or "").strip()
        ctx = db.contexts.get_context_by_topic(topic, as_of=0)
        if not ctx:
            return _json_error(ErrorCode.INVALID_ARGS, "target context not found")
        related = list(getattr(ctx, "related_files", []) or [])
        if path and path not in related:
            related.append(path)
        cur.execute(
            "UPDATE contexts SET related_files_json = ?, updated_ts = ? WHERE topic = ?",
            (json.dumps(related, ensure_ascii=False), now, topic),
        )
        conn.commit()
        return {
            "action": "relink",
            "type": "context",
            "memory_ref": f"context:{topic}",
            "linked_path": path,
            "updated": int(cur.rowcount or 0),
            "warnings": [],
        }

    if kind and kind != "snippet":
        return _json_error(ErrorCode.INVALID_ARGS, "memory_ref kind/type mismatch")
    try:
        snippet_id = int(ident or "0")
    except Exception:
        return _json_error(ErrorCode.INVALID_ARGS, "snippet relink requires numeric id")
    if snippet_id <= 0:
        return _json_error(ErrorCode.INVALID_ARGS, "snippet relink requires memory_ref=snippet:<id>")
    span = decoded.get("span")
    if not isinstance(span, Sequence) or len(span) < 2:
        return _json_error(ErrorCode.INVALID_ARGS, "new_context_ref is missing span")
    start_line = int(span[0])
    end_line = int(span[1])
    path = str(decoded.get("path") or "").strip()
    cur.execute(
        "UPDATE snippets SET path = ?, start_line = ?, end_line = ?, updated_ts = ? WHERE id = ?",
        (path, start_line, end_line, now, snippet_id),
    )
    conn.commit()
    return {
        "action": "relink",
        "type": "snippet",
        "memory_ref": f"snippet:{snippet_id}",
        "linked_path": path,
        "start_line": start_line,
        "end_line": end_line,
        "updated": int(cur.rowcount or 0),
        "warnings": [],
    }


def _extract_json_from_response(response: ToolResult) -> ToolResult:
    content = response.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, Mapping):
            text = str(first.get("text") or "")
            if text.startswith("{"):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, Mapping):
                        return dict(parsed)
                except Exception:
                    pass
    return {"error": {"code": ErrorCode.DB_ERROR.value, "message": "schema check failed"}, "isError": True}


def execute_knowledge(args: object, db: object, roots: list[str], indexer: object = None) -> ToolResult:
    if not isinstance(args, Mapping):
        return invalid_args_response("knowledge", "args must be an object")

    action = str(args.get("action") or "").strip().lower()
    alias_used = False
    if action == "search":
        action = "recall"
        alias_used = True

    if action not in {"save", "recall", "delete", "list", "relink"}:
        return invalid_args_response("knowledge", "action must be one of: save, recall, delete, list, relink")

    try:
        if action == "save":
            payload = _execute_save(args, db, roots)
        elif action == "recall":
            payload = _execute_recall(args, db, roots, alias_used)
        elif action == "list":
            payload = _execute_list(args, db, roots)
        elif action == "delete":
            payload = _execute_delete(args, db)
        elif action == "relink":
            payload = _execute_relink(args, db, roots)
        else:
            payload = _json_error(ErrorCode.INVALID_ARGS, f"action '{action}' is not implemented yet")
    except Exception as exc:
        payload = _json_error(ErrorCode.DB_ERROR, str(exc))

    if payload.get("isError"):
        error_obj = payload.get("error", {})
        if not isinstance(error_obj, Mapping):
            error_obj = {}
        raw_code = str(error_obj.get("code") or "").strip()
        raw_message = str(error_obj.get("message") or "").strip()
        code = raw_code or ErrorCode.INTERNAL.value
        msg = raw_message or "Knowledge operation failed"
        payload = {
            **payload,
            "error": {
                "code": code,
                "message": msg,
            },
            "isError": True,
        }
        return mcp_response(
            "knowledge",
            lambda: pack_error("knowledge", code, msg),
            lambda: payload,
        )

    def build_pack() -> str:
        results = payload.get("results", [])
        returned = len(results) if isinstance(results, list) else 1
        lines = [
            pack_header(
                "knowledge",
                {
                    "action": pack_encode_id(payload.get("action", "")),
                    "type": pack_encode_id(payload.get("type", "")),
                },
                returned=returned,
            )
        ]
        for warning in payload.get("warnings", []):
            lines.append(pack_line("m", {"warning": pack_encode_text(warning)}))
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, Mapping):
                    continue
                key = str(item.get("topic") or item.get("tag") or "")
                lines.append(pack_line("r", {"key": pack_encode_id(key)}))
        elif isinstance(payload.get("saved"), Mapping):
            saved = payload["saved"]
            key = str(saved.get("topic") or saved.get("tag") or "")
            lines.append(pack_line("r", {"key": pack_encode_id(key)}))
        return "\n".join(lines)

    return mcp_response("knowledge", build_pack, lambda: payload)
