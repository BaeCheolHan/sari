import zlib
from typing import Any

def _compress(text: str) -> bytes:
    if not text: return b""
    return zlib.compress(text.encode("utf-8"), level=6)

def _decompress(data: Any) -> str:
    if not data: return ""
    if isinstance(data, str): return data # legacy
    try:
        return zlib.decompress(data).decode("utf-8")
    except Exception:
        return str(data)
