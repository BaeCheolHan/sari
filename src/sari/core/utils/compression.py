import zlib
from typing import Any, Union


def _compress(text: Union[str, bytes]) -> bytes:
    if not text:
        return b""
    if isinstance(text, bytes):
        if text.startswith(b"ZLIB\0"):
            return text
        return b"ZLIB\0" + zlib.compress(text, level=6)
    return b"ZLIB\0" + \
        zlib.compress(text.encode("utf-8", errors="ignore"), level=6)


def _decompress(data: Any) -> str:
    if not data:
        return ""
    if isinstance(data, str):
        return data

    raw_data = data
    if isinstance(data, bytes) and data.startswith(b"ZLIB\0"):
        raw_data = data[5:]

    try:
        return zlib.decompress(raw_data).decode("utf-8", errors="ignore")
    except Exception:
        return str(data)
