"""세션 키 해석 유틸을 제공한다."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Mapping


def _normalize_text(value: object) -> str:
    """임의 입력을 공백 제거 문자열로 변환한다."""
    return str(value or "").strip()


def canonicalize_workspace_root(root: object) -> str:
    """워크스페이스 루트를 정규화한다."""
    text = _normalize_text(root)
    if text == "":
        return ""
    path = Path(text).expanduser()
    path = path.resolve(strict=False)
    normalized = path.as_posix()
    if normalized != "/":
        normalized = normalized.rstrip("/")
    return normalized


def workspace_hash(roots: list[str]) -> str:
    """워크스페이스 루트 목록의 대표 해시를 반환한다."""
    primary = ""
    for root in roots:
        primary = canonicalize_workspace_root(root)
        if primary != "":
            break
    digest = hashlib.sha256(primary.encode("utf-8")).hexdigest()
    return digest[:12]


def resolve_session_key(
    args: Mapping[str, object] | object,
    roots: list[str],
) -> str:
    """요청 인자와 roots를 기반으로 세션 키를 생성한다."""
    args_map = args if isinstance(args, Mapping) else {}
    workspace = workspace_hash(roots)
    session_id = _normalize_text(args_map.get("session_id")) if isinstance(args_map, Mapping) else ""
    if session_id != "":
        return f"ws:{workspace}:sid:{session_id}"
    connection_id = _normalize_text(args_map.get("connection_id")) if isinstance(args_map, Mapping) else ""
    if connection_id != "":
        return f"ws:{workspace}:conn:{connection_id}"
    return f"ws:{workspace}:conn:unknown"


def strict_session_id_enabled() -> bool:
    """strict session_id 강제 여부를 반환한다."""
    return _normalize_text(os.environ.get("SARI_STRICT_SESSION_ID")).lower() in {"1", "true", "yes", "on"}

