import os
from typing import Dict, Optional
from .base import BaseParser
from .python import PythonParser
from .generic import GenericRegexParser
from .common import _safe_compile


class ParserFactory:
    """
    파일 확장자에 따라 적절한 파서 객체를 생성하고 캐싱하는 팩토리 클래스입니다.
    Python, HCL 등 전용 파서가 있는 경우 이를 우선 사용하며,
    그 외의 경우 정규식 기반의 Generic 파서를 구성하여 반환합니다.
    """
    _parsers: Dict[str, BaseParser] = {}
    _lang_cache: Dict[str, Optional[str]] = {}
    try:
        _lang_cache_max: int = max(
            64,
            int(os.environ.get("SARI_PARSER_LANG_CACHE_MAX", "1024") or "1024"),
        )
    except Exception:
        _lang_cache_max = 1024
    _lang_map: Dict[str, str] = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".vue": "javascript",
        ".java": "java",
        ".kt": "kotlin",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "cpp",
        ".ex": "elixir",
        ".exs": "elixir",
        ".rb": "ruby",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".sql": "sql",
        ".tf": "hcl",
        ".hcl": "hcl",
    }

    @classmethod
    def get_parser(cls, ext: str) -> Optional[BaseParser]:
        """
        확장자에 맞는 파서 인스턴스를 반환합니다.
        이미 생성된 파서는 재사용(Singleton 패턴)하여 성능을 최적화합니다.
        """
        ext = (ext or "").lower()
        if ext == ".py":
            key = "python"
            if key not in cls._parsers:
                cls._parsers[key] = PythonParser()
            return cls._parsers[key]

        if ext in (".tf", ".hcl"):
            from .generic import HCLRegexParser
            key = "hcl_regex"
            if key not in cls._parsers:
                # Provide dummy config for parent GenericRegexParser
                dummy = {
                    "re_class": _safe_compile(r""),
                    "re_method": _safe_compile(r"")}
                cls._parsers[key] = HCLRegexParser(dummy, ext)
            return cls._parsers[key]

        configs = {
            ".java": {
                "re_class": _safe_compile(r"\b(class|interface|enum|record|@interface)\s+([a-zA-Z0-9_]+)"),
                "re_method": _safe_compile(r"(?:\b(?:public|protected|private|static|final|native|synchronized|abstract|transient|@\w+(?:\([^)]*\))?)\s+)*[\w<>\[\]\s,\?]+\s+(\w+)\s*\("),
            },
            ".kt": {"re_class": _safe_compile(r"\b(class|interface|enum|object|data\s+class|sealed\s+class)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"\bfun\s+(?:<[^>]+>\s+)?([a-zA-Z0-9_]+)\b\s*\(")},
            ".go": {"re_class": _safe_compile(r"\b(type|struct|interface)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"\bfunc\s+(?:\([^)]+\)\s+)?([a-zA-Z0-9_]+)\b\s*\("), "method_kind": "function"},
            ".cpp": {"re_class": _safe_compile(r"\b(class|struct|enum|namespace)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:[a-zA-Z0-9_:<>]+\s+)?\b([a-zA-Z0-9_]+)\b\s*\(")},
            ".cs": {"re_class": _safe_compile(r"\b(class|struct|interface|enum|record)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"\b(?:public|private|protected|internal|static|virtual|override|async|task)\s+[\w<>\[\]\s]+\s+([a-zA-Z0-9_]+)\s*\(")},
            ".js": {"re_class": _safe_compile(r"\b(class)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")},
            ".jsx": {"re_class": _safe_compile(r"\b(class)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")},
            ".ts": {"re_class": _safe_compile(r"\b(class|interface|enum)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")},
            ".tsx": {"re_class": _safe_compile(r"\b(class|interface|enum)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b(?!if|for|while|switch|catch)([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")},
            ".vue": {"re_class": _safe_compile(r"\b(class)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]{2,})\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b(?!if|for|while|switch|catch)([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")},
            ".rs": {"re_class": _safe_compile(r"\b(struct|enum|trait|union|mod)\s+([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"\bfn\s+([a-zA-Z0-9_]+)\b\s*[<(]")},
            ".ex": {"re_class": _safe_compile(r"\bdefmodule\s+([a-zA-Z0-9_.]+)"), "re_method": _safe_compile(r"\bdef(?:p)?\s+([a-zA-Z0-9_!?]+)\b\s*[({]|\bdef(?:p)?\s+([a-zA-Z0-9_!?]+)\s*,\s*do")},
            ".exs": {"re_class": _safe_compile(r"\bdefmodule\s+([a-zA-Z0-9_.]+)"), "re_method": _safe_compile(r"\bdef(?:p)?\s+([a-zA-Z0-9_!?]+)\b\s*[({]|\bdef(?:p)?\s+([a-zA-Z0-9_!?]+)\s*,\s*do")},
            ".rb": {"re_class": _safe_compile(r"\b(class|module)\s+([a-zA-Z0-9_:]+)"), "re_method": _safe_compile(r"\bdef\s+([a-zA-Z0-9_!?]+)")},
            ".yaml": {"re_class": _safe_compile(r"^kind:\s*([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"^\s*name:\s*([a-zA-Z0-9_-]+)")},
            ".yml": {"re_class": _safe_compile(r"^kind:\s*([a-zA-Z0-9_]+)"), "re_method": _safe_compile(r"^\s*name:\s*([a-zA-Z0-9_-]+)")},
            ".sql": {
                "re_class": _safe_compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(TABLE|VIEW|INDEX|PROCEDURE|FUNCTION)\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_]+)"),
                # Optional: constraints as methods/properties
                "re_method": _safe_compile(r"\bCONSTRAINT\s+([a-zA-Z0-9_]+)")
            },
            ".tf": {
                "re_class": _safe_compile(r"^(resource|module|variable|output|data)\s+(?:\"[^\"]+\"\s+)?\"([a-zA-Z0-9_-]+)\""),
                "re_method": _safe_compile(r"^\s*(source|type)\s*=\s*\"([^\"]+)\"")
            },
            ".hcl": {
                "re_class": _safe_compile(r"^(resource|module|variable|output|data)\s+(?:\"[^\"]+\"\s+)?\"([a-zA-Z0-9_-]+)\""),
                "re_method": _safe_compile(r"^\s*(source|type)\s*=\s*\"([^\"]+)\"")
            }
        }
        if ext in configs:
            key = f"generic:{ext}"
            if key not in cls._parsers:
                cls._parsers[key] = GenericRegexParser(configs[ext], ext)
            return cls._parsers[key]
        return None

    @classmethod
    def get_language(cls, ext: str) -> Optional[str]:
        """
        파일 확장자를 Tree-sitter에서 사용하는 언어 식별자로 변환합니다.
        """
        key = (ext or "").lower()
        if key in cls._lang_cache:
            return cls._lang_cache[key]
        val = cls._lang_map.get(key)
        cls._lang_cache[key] = val
        while len(cls._lang_cache) > int(cls._lang_cache_max or 0):
            cls._lang_cache.pop(next(iter(cls._lang_cache)), None)
        return val
