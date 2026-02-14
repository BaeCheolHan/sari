from __future__ import annotations

from threading import RLock

_LOCK = RLock()
_UNKNOWN_TOOL_ERROR_COUNT = 0


def note_unknown_tool_error() -> None:
    global _UNKNOWN_TOOL_ERROR_COUNT
    with _LOCK:
        _UNKNOWN_TOOL_ERROR_COUNT += 1


def snapshot_error_contract_metrics() -> dict[str, int]:
    with _LOCK:
        return {"unknown_tool_error_count": int(_UNKNOWN_TOOL_ERROR_COUNT)}


def reset_error_contract_metrics_for_tests() -> None:
    global _UNKNOWN_TOOL_ERROR_COUNT
    with _LOCK:
        _UNKNOWN_TOOL_ERROR_COUNT = 0
