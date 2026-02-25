"""MCP 도구 공통 유틸리티를 제공한다."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sari.core.models import ErrorResponseDTO
from sari.mcp.tools.arg_normalizer import ARG_META_KEY
from sari.mcp.tools.pack1 import pack1_error
from sari.mcp.tools.pack1_builder import Pack1EnvelopeBuilder


def pack1_items_success(
    items: list[dict[str, object]],
    *,
    cache_hit: bool = False,
    stabilization: dict[str, object] | None = None,
    warnings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """표준 pack1 성공 응답을 생성한다."""
    return Pack1EnvelopeBuilder().build_success(
        items=items,
        candidate_count=len(items),
        resolved_count=len(items),
        cache_hit=cache_hit,
        errors=[],
        stabilization=stabilization,
        warnings=warnings,
    )


def extract_arg_meta(arguments: dict[str, object]) -> tuple[list[str], dict[str, str]]:
    """정규화 메타(received_keys/normalized_from)를 추출한다."""
    raw_meta = arguments.get(ARG_META_KEY)
    if not isinstance(raw_meta, dict):
        return ([], {})
    received_raw = raw_meta.get("received_keys")
    normalized_raw = raw_meta.get("normalized_from")
    received_keys: list[str] = []
    normalized_from: dict[str, str] = {}
    if isinstance(received_raw, list):
        received_keys = [str(item) for item in received_raw]
    if isinstance(normalized_raw, dict):
        normalized_from = {str(key): str(value) for key, value in normalized_raw.items()}
    return (received_keys, normalized_from)


def argument_error(
    *,
    code: str,
    message: str,
    arguments: dict[str, object],
    expected: list[str],
    example: dict[str, object],
) -> dict[str, object]:
    """자기설명형 인자 오류 응답을 생성한다."""
    received_keys, normalized_from = extract_arg_meta(arguments)
    return pack1_error(
        ErrorResponseDTO(code=code, message=message),
        expected=expected,
        received=received_keys,
        example=example,
        normalized_from=normalized_from,
    )


def resolve_symbol_key(arguments: dict[str, object]) -> str | None:
    """심볼 키 입력(symbol/symbol_id/sid/name/target)을 단일 문자열로 정규화한다."""
    for key in ("symbol", "symbol_id", "sid", "name", "target"):
        raw = arguments.get(key)
        if isinstance(raw, str) and raw.strip() != "":
            return raw.strip()
    return None


def resolve_source_path(repo_root: str, raw_path: str) -> Path:
    """입력 path를 저장소 기준 절대 경로로 변환한다."""
    source = Path(raw_path).expanduser()
    if source.is_absolute():
        return source
    return (Path(repo_root) / raw_path).resolve()


def normalize_source_path(repo_root: str, source_path: Path) -> str:
    """소스 경로를 저장소 기준 상대경로 우선으로 정규화한다."""
    try:
        return str(source_path.resolve().relative_to(Path(repo_root).resolve()))
    except ValueError:
        return str(source_path.resolve())


def content_hash(text: str) -> str:
    """텍스트 본문의 짧은 해시를 계산한다."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
