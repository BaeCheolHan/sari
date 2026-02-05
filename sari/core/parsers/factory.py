from typing import Dict, Optional
from .base import BaseParser
from .python import PythonParser
from .generic import GenericRegexParser
from .common import _safe_compile

class ParserFactory:
    _parsers: Dict[str, BaseParser] = {}
    _lang_cache: Dict[str, Optional[str]] = {}
    _lang_map: Dict[str, str] = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".java": "java",
        ".kt": "kotlin",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "cpp",
    }

    @classmethod
    def get_parser(cls, ext: str) -> Optional[BaseParser]:
        ext = (ext or "").lower()
        if ext == ".py":
            key = "python"
            if key not in cls._parsers:
                cls._parsers[key] = PythonParser()
            return cls._parsers[key]
        configs = {
            ".java": {"re_class": _safe_compile(r"\b(class|interface|enum|record)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:[a-zA-Z0-9_<>,.\[\]\s]+?\s+)?\b([a-zA-Z0-9_]+)\b\s*\(")},
            ".kt": {"re_class": _safe_compile(r"\b(class|interface|enum|object|data\s+class)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"\bfun\s+([a-zA-Z0-9_]+)\b\s*\(")},
            ".go": {"re_class": _safe_compile(r"\b(type|struct|interface)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"\bfunc\s+(?:[^)]+\)\s+)?([a-zA-Z0-9_]+)\b\s*\("), "method_kind": "function"},
            ".cpp": {"re_class": _safe_compile(r"\b(class|struct|enum)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:[a-zA-Z0-9_:<>]+\s+)?\b([a-zA-Z0-9_]+)\b\s*\(")},
            ".h": {"re_class": _safe_compile(r"\b(class|struct|enum)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:[a-zA-Z0-9_:<>]+\s+)?\b([a-zA-Z0-9_]+)\b\s*\(")},
            ".js": {"re_class": _safe_compile(r"\b(class)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(")},
            ".jsx": {"re_class": _safe_compile(r"\b(class)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(")},
            ".ts": {"re_class": _safe_compile(r"\b(class|interface|enum)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(")},
            ".tsx": {"re_class": _safe_compile(r"\b(class|interface|enum)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(")}
        }
        if ext in configs:
            key = f"generic:{ext}"
            if key not in cls._parsers: cls._parsers[key] = GenericRegexParser(configs[ext], ext)
            return cls._parsers[key]
        return None

    @classmethod
    def get_language(cls, ext: str) -> Optional[str]:
        key = (ext or "").lower()
        if key in cls._lang_cache:
            return cls._lang_cache[key]
        val = cls._lang_map.get(key)
        cls._lang_cache[key] = val
        return val
