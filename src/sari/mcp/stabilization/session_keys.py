from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Mapping


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def canonicalize_workspace_root(root: object) -> str:
    text = _normalize_text(root)
    if not text:
        return ""
    path = Path(text).expanduser()
    try:
        path = path.resolve(strict=False)
    except Exception:
        path = Path(os.path.abspath(str(path)))
    normalized = path.as_posix()
    if normalized != "/":
        normalized = normalized.rstrip("/")
    return normalized


def workspace_hash(roots: list[str]) -> str:
    primary = ""
    for root in roots:
        primary = canonicalize_workspace_root(root)
        if primary:
            break
    digest = hashlib.sha256(primary.encode("utf-8")).hexdigest()
    return digest[:12]


def resolve_session_key(
    args: Mapping[str, object] | object,
    roots: list[str],
) -> str:
    args_map = args if isinstance(args, Mapping) else {}
    ws = workspace_hash(roots)
    session_id = _normalize_text(args_map.get("session_id")) if isinstance(args_map, Mapping) else ""
    if session_id:
        return f"ws:{ws}:sid:{session_id}"
    connection_id = _normalize_text(args_map.get("connection_id")) if isinstance(args_map, Mapping) else ""
    if connection_id:
        return f"ws:{ws}:conn:{connection_id}"
    return f"ws:{ws}:conn:unknown"


def strict_session_id_enabled() -> bool:
    return _normalize_text(os.environ.get("SARI_STRICT_SESSION_ID")).lower() in {"1", "true", "yes", "on"}
