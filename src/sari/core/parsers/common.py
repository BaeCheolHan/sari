import re
import hashlib
from typing import Dict, Optional, Any

def _safe_compile(pattern: str, flags: int = 0, fallback: Optional[str] = None) -> re.Pattern:
    """
    정규식을 안전하게 컴파일합니다. 
    패턴에 오류가 있는 경우 fallback 패턴이나 '매칭 불가' 패턴(a^)을 반환하여 에러를 방지합니다.
    """
    try:
        return re.compile(pattern, flags)
    except re.error:
        if fallback:
            try: return re.compile(fallback, flags)
            except re.error: pass
        return re.compile(r"a^")

def _qualname(parent: str, name: str) -> str:
    """부모 이름과 현재 이름을 결합하여 정규화된 이름(Qualname)을 생성합니다."""
    parent = (parent or "").strip()
    if not parent:
        return name
    return f"{parent}.{name}"

def _symbol_id(path: str, kind: str, qualname: str) -> str:
    """파일 경로, 종류, 이름을 기반으로 심볼의 고유 식별자(SHA1)를 생성합니다."""
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
