import os
import json
import socket
import threading
from typing import Optional, Dict, Any, Tuple

try:
    import fcntl
except ImportError:
    fcntl = None

def flock(file_obj, exclusive: bool = True):
    """Unified file locking for POSIX and Windows."""
    if fcntl:
        mask = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(file_obj.fileno(), mask)
    else:
        try:
            import msvcrt
            msvcrt.locking(file_obj.fileno(), 1, 1) # Simple block lock
        except ImportError:
            pass

def funlock(file_obj):
    """Unified file unlock."""
    if fcntl:
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
    else:
        try:
            import msvcrt
            msvcrt.locking(file_obj.fileno(), 0, 1)
        except ImportError:
            pass

def parse_mcp_headers(stream) -> Dict[str, str]:
    """Reusable header parser for MCP/JSON-RPC framing."""
    headers = {}
    while True:
        line = stream.readline()
        if not line: break
        line = line.strip()
        if not line: break
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.strip().lower().decode()] = v.strip().decode()
    return headers

def read_mcp_message(stream, headers: Dict[str, str], max_size: int = 10 * 1024 * 1024) -> Optional[bytes]:
    """Safely read body based on Content-Length header."""
    try:
        content_length = int(headers.get("content-length", 0))
        if content_length <= 0 or content_length > max_size:
            return None
        return stream.read(content_length)
    except (ValueError, TypeError):
        return None
