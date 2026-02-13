import datetime as _dt
import os
import re
import time
from typing import Callable, Optional


def parse_log_line_ts(text: str) -> float:
    raw = str(text or "")
    m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{1,6})?)", raw)
    if not m:
        return 0.0
    token = m.group(1)
    for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(token, fmt).timestamp()
        except Exception:
            continue
    return 0.0


def read_recent_log_error_entries(
    *,
    limit: int = 50,
    parse_ts: Callable[[str], float] = parse_log_line_ts,
) -> list[dict[str, object]]:
    try:
        from sari.core.workspace import WorkspaceManager

        env_log_dir = os.environ.get("SARI_LOG_DIR")
        log_dir = os.path.expanduser(env_log_dir) if env_log_dir else str(WorkspaceManager.get_global_log_dir())
        log_file = os.path.join(log_dir, "daemon.log")
        if not os.path.exists(log_file):
            return []
        file_size = os.path.getsize(log_file)
        read_size = min(file_size, 1024 * 1024)
        with open(log_file, "rb") as f:
            if file_size > read_size:
                f.seek(file_size - read_size)
            chunk = f.read().decode("utf-8", errors="ignore")
        lines = chunk.splitlines()
        out: list[dict[str, object]] = []
        level_pat = re.compile(r"\b(ERROR|CRITICAL)\b")
        for line in reversed(lines):
            text = str(line or "").strip()
            if not text:
                continue
            if level_pat.search(text):
                out.append({"text": text, "ts": float(parse_ts(text) or 0.0)})
            if len(out) >= max(1, int(limit)):
                break
        out.reverse()
        return out
    except Exception:
        return []


def build_errors_payload(
    *,
    limit: int = 50,
    source: str = "all",
    reason_codes: Optional[set[str]] = None,
    since_sec: int = 0,
    warning_sink_obj,
    read_log_entries: Callable[[int], list[dict[str, object]]],
    status_warning_counts_provider: Callable[[], dict[str, int]],
) -> dict[str, object]:
    lim = max(1, min(int(limit or 50), 200))
    source_norm = str(source or "all").strip().lower()
    if source_norm not in {"all", "log", "warning"}:
        source_norm = "all"
    reason_filter = {str(rc).strip() for rc in (reason_codes or set()) if str(rc).strip()}
    since = max(0, int(since_sec or 0))
    cutoff_ts = time.time() - since if since > 0 else 0.0

    warnings_recent = warning_sink_obj.warnings_recent()
    if isinstance(warnings_recent, list):
        filtered_warnings = []
        for item in warnings_recent:
            if not isinstance(item, dict):
                continue
            code = str(item.get("reason_code") or "")
            ts = float(item.get("ts") or 0.0)
            if reason_filter and code not in reason_filter:
                continue
            if cutoff_ts > 0 and ts > 0 and ts < cutoff_ts:
                continue
            filtered_warnings.append(item)
        warnings_recent = filtered_warnings
    else:
        warnings_recent = []

    log_entries = read_log_entries(lim)
    if cutoff_ts > 0:
        log_entries = [e for e in log_entries if float(e.get("ts") or 0.0) >= cutoff_ts]
    log_errors = [str(e.get("text") or "") for e in log_entries]
    if source_norm == "log":
        warnings_recent = []
    elif source_norm == "warning":
        log_entries = []
        log_errors = []
    return {
        "ok": True,
        "limit": lim,
        "source": source_norm,
        "reason_codes": sorted(list(reason_filter)),
        "since_sec": since,
        "warnings_recent": warnings_recent[-lim:] if isinstance(warnings_recent, list) else [],
        "warning_counts": warning_sink_obj.warning_counts(),
        "status_warning_counts": status_warning_counts_provider(),
        "log_errors": log_errors[-lim:],
        "log_error_entries": log_entries[-lim:],
    }
