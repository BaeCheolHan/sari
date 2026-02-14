from __future__ import annotations

import time
import os
import json
from pathlib import Path
from threading import RLock
from typing import TypedDict


class FallbackStat(TypedDict, total=False):
    fallback_id: str
    enter_count: int
    exit_count: int
    active: bool
    last_trigger: str
    last_exit_condition: str
    last_event_ts: float
    avg_recovery_sec: float


FALLBACK_TAXONOMY: dict[str, dict[str, str]] = {
    "registry_path_fallback": {
        "trigger": "default registry path is not writable",
        "exit_condition": "default registry path becomes writable again",
    },
    "port_auto_fallback": {
        "trigger": "requested daemon port is occupied and strategy=auto",
        "exit_condition": "fallback daemon endpoint is selected and launched",
    },
    "search_text_fallback": {
        "trigger": "engine search failed and fallback search path is used",
        "exit_condition": "fallback search result returned",
    },
    "workspace_normalization_fallback": {
        "trigger": "workspace path normalization raised error",
        "exit_condition": "fallback-normalized path computed",
    },
    "symbol_implementation_text_fallback": {
        "trigger": "direct symbol relation lookup returned no rows or failed",
        "exit_condition": "text-pattern fallback search returned",
    },
    "engine_embedded_unavailable_fallback": {
        "trigger": "embedded engine was requested but tantivy is unavailable",
        "exit_condition": "sqlite engine selected",
    },
    "engine_default_sqlite_fallback": {
        "trigger": "tantivy unavailable during default engine selection",
        "exit_condition": "sqlite engine selected",
    },
}

_LOCK = RLock()
_STATS: dict[str, dict[str, float | int | bool | str]] = {}
_ACTIVE_SINCE: dict[str, float] = {}
_LOADED = False


def _metrics_file() -> Path:
    env_path = str(os.environ.get("SARI_FALLBACK_METRICS_FILE", "") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return (Path.home() / ".local" / "share" / "sari" / "fallback_metrics.json")


def _load_if_needed() -> None:
    global _LOADED
    if _LOADED:
        return
    path = _metrics_file()
    try:
        if not path.exists():
            _LOADED = True
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rows") if isinstance(payload, dict) else []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                fid = str(row.get("fallback_id") or "").strip()
                if not fid:
                    continue
                stat = _ensure_stat(fid)
                stat["enter_count"] = int(row.get("enter_count", 0) or 0)
                stat["exit_count"] = int(row.get("exit_count", 0) or 0)
                stat["active"] = bool(row.get("active", False))
                stat["last_trigger"] = str(row.get("last_trigger", "") or "")
                stat["last_exit_condition"] = str(row.get("last_exit_condition", "") or "")
                stat["last_event_ts"] = float(row.get("last_event_ts", 0.0) or 0.0)
                stat["total_recovery_sec"] = (
                    float(row.get("avg_recovery_sec", 0.0) or 0.0) * int(stat.get("exit_count", 0) or 0)
                )
    except Exception:
        pass
    _LOADED = True


def _persist() -> None:
    path = _metrics_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot_fallback_metrics(), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def fallback_taxonomy() -> dict[str, dict[str, str]]:
    return dict(FALLBACK_TAXONOMY)


def _ensure_stat(fallback_id: str) -> dict[str, float | int | bool | str]:
    stat = _STATS.get(fallback_id)
    if stat is None:
        stat = {
            "fallback_id": fallback_id,
            "enter_count": 0,
            "exit_count": 0,
            "active": False,
            "last_trigger": "",
            "last_exit_condition": "",
            "last_event_ts": 0.0,
            "total_recovery_sec": 0.0,
        }
        _STATS[fallback_id] = stat
    return stat


def note_fallback_enter(fallback_id: str, *, trigger: str = "") -> None:
    now = time.time()
    with _LOCK:
        _load_if_needed()
        stat = _ensure_stat(fallback_id)
        stat["enter_count"] = int(stat.get("enter_count", 0) or 0) + 1
        stat["active"] = True
        stat["last_trigger"] = str(trigger or "")
        stat["last_event_ts"] = now
        _ACTIVE_SINCE[fallback_id] = now
        _persist()


def note_fallback_exit(fallback_id: str, *, exit_condition: str = "") -> None:
    now = time.time()
    with _LOCK:
        _load_if_needed()
        stat = _ensure_stat(fallback_id)
        stat["exit_count"] = int(stat.get("exit_count", 0) or 0) + 1
        stat["active"] = False
        stat["last_exit_condition"] = str(exit_condition or "")
        stat["last_event_ts"] = now
        started = _ACTIVE_SINCE.pop(fallback_id, None)
        if started is not None and started > 0:
            elapsed = max(0.0, now - started)
            stat["total_recovery_sec"] = float(stat.get("total_recovery_sec", 0.0) or 0.0) + elapsed
        _persist()


def note_fallback_event(fallback_id: str, *, trigger: str = "", exit_condition: str = "") -> None:
    note_fallback_enter(fallback_id, trigger=trigger)
    note_fallback_exit(fallback_id, exit_condition=exit_condition)


def snapshot_fallback_metrics() -> dict[str, object]:
    with _LOCK:
        _load_if_needed()
        rows: list[FallbackStat] = []
        for fid, raw in _STATS.items():
            exit_count = int(raw.get("exit_count", 0) or 0)
            total_recovery = float(raw.get("total_recovery_sec", 0.0) or 0.0)
            avg_recovery = total_recovery / exit_count if exit_count > 0 else 0.0
            rows.append(
                {
                    "fallback_id": fid,
                    "enter_count": int(raw.get("enter_count", 0) or 0),
                    "exit_count": exit_count,
                    "active": bool(raw.get("active", False)),
                    "last_trigger": str(raw.get("last_trigger", "") or ""),
                    "last_exit_condition": str(raw.get("last_exit_condition", "") or ""),
                    "last_event_ts": float(raw.get("last_event_ts", 0.0) or 0.0),
                    "avg_recovery_sec": avg_recovery,
                }
            )
    rows.sort(key=lambda item: str(item.get("fallback_id") or ""))
    return {
        "total_fallback_types_seen": len(rows),
        "active_fallback_types": sum(1 for r in rows if bool(r.get("active"))),
        "rows": rows,
    }


def reset_fallback_metrics_for_tests() -> None:
    global _LOADED
    with _LOCK:
        _STATS.clear()
        _ACTIVE_SINCE.clear()
        _LOADED = True
        try:
            _metrics_file().unlink(missing_ok=True)
        except Exception:
            pass
