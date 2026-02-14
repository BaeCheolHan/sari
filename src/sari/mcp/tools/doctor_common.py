"""Shared helpers for doctor tool checks."""

from typing import TypeAlias

DoctorResult: TypeAlias = dict[str, object]


def result(name: str, passed: bool, error: str = "", warn: bool = False) -> DoctorResult:
    return {"name": name, "passed": passed, "error": error, "warn": warn}


def compact_error_message(exc: object, fallback: str = "unknown error", max_len: int = 300) -> str:
    raw = str(exc).strip() if exc is not None else ""
    if not raw:
        return fallback
    text = " ".join(raw.replace("\r", " ").replace("\n", " ").split())
    if len(text) > max_len:
        return text[:max_len]
    return text


def row_get(row: object, key: str, index: int, default: object = None) -> object:
    if row is None:
        return default
    try:
        if hasattr(row, "keys"):
            return row[key]
    except Exception:
        pass
    if isinstance(row, (list, tuple)) and len(row) > index:
        return row[index]
    return default


def safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def safe_float(value: object, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def safe_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def safe_pragma_table_name(name: str) -> str:
    allowed = {
        "symbols",
        "symbol_relations",
        "files",
        "roots",
        "failed_tasks",
        "snippets",
        "snippet_versions",
        "contexts",
    }
    if name in allowed:
        return name
    raise ValueError(f"Unsafe or unauthorized table name for PRAGMA: {name}")
