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
        ext = (ext or "").lower()
        if ext == ".py":
            key = "python"
            if key not in cls._parsers:
                cls._parsers[key] = PythonParser()
            if key not in cls._parsers:
                cls._parsers[key] = PythonParser()
            return cls._parsers[key]
        
        if ext in (".tf", ".hcl"):
            from .generic import HCLRegexParser
            key = "hcl_regex"
            if key not in cls._parsers:
                # Provide dummy config for parent GenericRegexParser
                dummy = {"re_class": _safe_compile(r""), "re_method": _safe_compile(r"")}
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
                "re_method": _safe_compile(r"\bCONSTRAINT\s+([a-zA-Z0-9_]+)")  # Optional: constraints as methods/properties
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
