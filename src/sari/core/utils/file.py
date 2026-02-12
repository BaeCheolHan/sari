from pathlib import Path
from typing import Optional

_TEXT_SAMPLE_BYTES = 8192

def _sample_file(path: Path, size: int) -> bytes:
    try:
        with path.open("rb") as f:
            head = f.read(_TEXT_SAMPLE_BYTES)
            if size <= _TEXT_SAMPLE_BYTES:
                return head
            try:
                f.seek(max(0, size - _TEXT_SAMPLE_BYTES))
            except Exception:
                return head
            tail = f.read(_TEXT_SAMPLE_BYTES)
            return head + tail
    except Exception:
        return b""

def _printable_ratio(sample: bytes, policy: str = "strong") -> float:
    if not sample:
        return 1.0
    if b"\x00" in sample:
        return 0.0
    try:
        text = sample.decode("utf-8") if policy == "strong" else sample.decode("utf-8", errors="ignore")
    except UnicodeDecodeError:
        return 0.0
    printable = 0
    total = len(text)
    for ch in text:
        if ch in ("\t", "\n", "\r") or ch.isprintable():
            printable += 1
    return printable / max(1, total)

def _is_minified(path: Path, text_sample: str) -> bool:
    if ".min." in path.name:
        return True
    if not text_sample:
        return False
    lines = text_sample.splitlines()
    if not lines:
        return len(text_sample) > 300
    total_len = sum(len(line_text) for line_text in lines)
    avg_len = total_len / max(1, len(lines))
    return avg_len > 300

def _parse_size(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    s = str(value).strip().lower()
    if not s:
        return default
    mult = 1
    if s.endswith("kb"):
        mult = 1024
        s = s[:-2]
    elif s.endswith("mb"):
        mult = 1024 * 1024
        s = s[:-2]
    elif s.endswith("gb"):
        mult = 1024 * 1024 * 1024
        s = s[:-2]
    elif s.endswith("tb"):
        mult = 1024 * 1024 * 1024 * 1024
        s = s[:-2]
    s = s.replace(",", "").replace("_", "")
    try:
        return int(float(s) * mult)
    except Exception:
        return default
