from typing import Dict, Optional
from .base import BaseParser
from .python import PythonParser
from .generic import GenericRegexParser
from .common import _safe_compile

class ParserFactory:
    """
    Priority 12: Formal Parser Registry for Plugin Extensibility.
    Restored all built-in language configurations.
    """
    _parsers: Dict[str, BaseParser] = {}
    _lang_cache: Dict[str, Optional[str]] = {}
    
    # Priority 11: Standard Language Mapping
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
    }

    @classmethod
    def register_parser(cls, ext: str, parser: BaseParser):
        """Allow external plugins to register custom parsers."""
        cls._parsers[ext.lower()] = parser

    @classmethod
    def get_parser(cls, ext: str) -> Optional[BaseParser]:
        ext = (ext or "").lower()
        
        # 1. Check registered parsers first (Cache or Plugins)
        if ext in cls._parsers:
            return cls._parsers[ext]
            
        # 2. Python specialized parser
        if ext == ".py":
            parser = PythonParser()
            cls.register_parser(ext, parser)
            return parser
            
        # 3. Built-in Regex Configurations (Restored)
        configs = {
            ".java": {
                "re_class": _safe_compile(r"\b(class|interface|enum|record|@interface)\s+([a-zA-Z0-9_]+)"),
                "re_method": _safe_compile(r"(?:(?:public|protected|private|static|final|native|synchronized|abstract|transient|@\w+(?:\([^)]*\))?)\s+)+[\w<>\[\]\s,\?]+\s+(\w+)\s*\("),
            },
            ".kt": {
                "re_class": _safe_compile(r"\b(class|interface|enum|object|data\s+class|sealed\s+class)\s+([a-zA-Z0-9_]+)"), 
                "re_method": _safe_compile(r"\bfun\s+(?:<[^>]+>\s+)?([a-zA-Z0-9_]+)\b\s*\(")
            },
            ".go": {
                "re_class": _safe_compile(r"\b(type|struct|interface)\s+([a-zA-Z0-9_]+)"), 
                "re_method": _safe_compile(r"\bfunc\s+(?:\([^)]+\)\s+)?([a-zA-Z0-9_]+)\b\s*\("), 
                "method_kind": "function"
            },
            ".cpp": {
                "re_class": _safe_compile(r"\b(class|struct|enum|namespace)\s+([a-zA-Z0-9_]+)"), 
                "re_method": _safe_compile(r"(?:[a-zA-Z0-9_:<>]+\s+)?\b([a-zA-Z0-9_]+)\b\s*\(")
            },
            ".js": {
                "re_class": _safe_compile(r"\b(class)\s+([a-zA-Z0-9_]+)"), 
                "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")
            },
            ".jsx": {
                "re_class": _safe_compile(r"\b(class)\s+([a-zA-Z0-9_]+)"), 
                "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")
            },
            ".ts": {
                "re_class": _safe_compile(r"\b(class|interface|enum)\s+([a-zA-Z0-9_]+)"), 
                "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")
            },
            ".tsx": {
                "re_class": _safe_compile(r"\b(class|interface|enum)\s+([a-zA-Z0-9_]+)"), 
                "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")
            },
            ".vue": {
                "re_class": _safe_compile(r"\b(class)\s+([a-zA-Z0-9_]+)"), 
                "re_method": _safe_compile(r"(?:async\s+)?function\s+([a-zA-Z0-9_]+)\b\s*\(|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?function\b|\b([a-zA-Z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|\b([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{")
            },
            ".rs": {
                "re_class": _safe_compile(r"\b(struct|enum|trait|union|mod)\s+([a-zA-Z0-9_]+)"), 
                "re_method": _safe_compile(r"\bfn\s+([a-zA-Z0-9_]+)\b\s*[<(]")
            },
            ".ex": {
                "re_class": _safe_compile(r"\bdefmodule\s+([a-zA-Z0-9_.]+)"), 
                "re_method": _safe_compile(r"\bdef(?:p)?\s+([a-zA-Z0-9_!?]+)\b\s*[({]|\bdef(?:p)?\s+([a-zA-Z0-9_!?]+)\s*,\s*do")
            },
            ".exs": {
                "re_class": _safe_compile(r"\bdefmodule\s+([a-zA-Z0-9_.]+)"), 
                "re_method": _safe_compile(r"\bdef(?:p)?\s+([a-zA-Z0-9_!?]+)\b\s*[({]|\bdef(?:p)?\s+([a-zA-Z0-9_!?]+)\s*,\s*do")
            }
        }
        
        if ext in configs:
            parser = GenericRegexParser(configs[ext], ext)
            cls.register_parser(ext, parser)
            return parser
            
        return None

    @classmethod
    def get_language(cls, ext: str) -> Optional[str]:
        key = (ext or "").lower()
        if key in cls._lang_cache:
            return cls._lang_cache[key]
        val = cls._lang_map.get(key)
        cls._lang_cache[key] = val
        return val