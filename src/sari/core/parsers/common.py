import re
import hashlib
from typing import Dict, Optional, Any

def _safe_compile(pattern: str, flags: int = 0, fallback: Optional[str] = None) -> re.Pattern:
    try:
        return re.compile(pattern, flags)
    except re.error:
        if fallback:
            try: return re.compile(fallback, flags)
            except re.error: pass
        return re.compile(r"a^")

def _qualname(parent: str, name: str) -> str:
    parent = (parent or "").strip()
    if not parent:
        return name
    return f"{parent}.{name}"

def _symbol_id(path: str, kind: str, qualname: str) -> str:
    base = f"{path}|{kind}|{qualname}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()

NORMALIZE_KIND_BY_EXT: Dict[str, Dict[str, str]] = {
    ".java": {"record": "class", "interface": "class"},
    ".kt": {"interface": "class", "object": "class", "data class": "class"},
    ".go": {},
    ".cpp": {},
    ".h": {},
    ".ts": {"interface": "class"},
    ".tsx": {"interface": "class"},
}
