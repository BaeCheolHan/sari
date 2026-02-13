"""Bootstrap helpers for MCP server runtime options."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RuntimeOptions:
    debug_enabled: bool
    dev_jsonl: bool
    force_content_length: bool
    queue_size: int
    max_workers: int


def parse_truthy_flag(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_runtime_options(
    env: Mapping[str, str],
    debug_default: bool,
    queue_size: int,
) -> RuntimeOptions:
    debug_enabled = bool(debug_default) or str(env.get("SARI_MCP_DEBUG", "0")) == "1"
    dev_jsonl = parse_truthy_flag(env.get("SARI_DEV_JSONL"))
    force_content_length = parse_truthy_flag(env.get("SARI_FORCE_CONTENT_LENGTH"))
    max_workers = int(env.get("SARI_MCP_WORKERS", "4") or 4)
    return RuntimeOptions(
        debug_enabled=debug_enabled,
        dev_jsonl=dev_jsonl,
        force_content_length=force_content_length,
        queue_size=int(queue_size),
        max_workers=max_workers,
    )
