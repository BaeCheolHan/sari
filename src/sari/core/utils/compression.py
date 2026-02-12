import zlib
from typing import Union


def _compress(text: Union[str, bytes]) -> bytes:
    if not text:
        return b""
    if isinstance(text, bytes):
        if text.startswith(b"ZLIB\0"):
            return text
        return b"ZLIB\0" + zlib.compress(text, level=6)
    return b"ZLIB\0" + \
        zlib.compress(text.encode("utf-8", errors="ignore"), level=6)


def _decompress(data: object) -> str:
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
        if isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("latin-1", errors="ignore")
        return str(data)
