"""Static registry for L3 language processors."""

from __future__ import annotations

from .l3_language_processor import L3LanguageProcessor
from .language_processors.default_processor import DefaultL3LanguageProcessor
from .language_processors.java_processor import JavaL3LanguageProcessor
from .language_processors.javascript_processor import JavaScriptL3LanguageProcessor
from .language_processors.kotlin_processor import KotlinL3LanguageProcessor
from .language_processors.python_processor import PythonL3LanguageProcessor
from .language_processors.scala_processor import ScalaL3LanguageProcessor
from .language_processors.typescript_processor import TypeScriptL3LanguageProcessor
from .language_processors.vue_processor import VueL3LanguageProcessor


class L3LanguageProcessorRegistry:
    """Path-based static processor registry."""

    def __init__(self) -> None:
        self._processors: tuple[L3LanguageProcessor, ...] = (
            VueL3LanguageProcessor(),
            PythonL3LanguageProcessor(),
            JavaL3LanguageProcessor(),
            KotlinL3LanguageProcessor(),
            ScalaL3LanguageProcessor(),
            TypeScriptL3LanguageProcessor(),
            JavaScriptL3LanguageProcessor(),
        )
        self._fallback: L3LanguageProcessor = DefaultL3LanguageProcessor()
        self._name_index: dict[str, L3LanguageProcessor] = {
            processor.name.strip().lower(): processor for processor in self._processors
        }

    def resolve(self, *, relative_path: str) -> L3LanguageProcessor:
        for processor in self._processors:
            if processor.supports_path(relative_path=relative_path):
                return processor
        return self._fallback

    def resolve_by_pattern_key(self, *, pattern_key: str) -> L3LanguageProcessor:
        target = str(pattern_key).strip().lower()
        if target == "":
            return self._fallback
        alias_map = {
            "py": "python",
            "python": "python",
            "java": "java",
            "kotlin": "kotlin",
            "scala": "scala",
            "javascript": "javascript",
            "js": "javascript",
            "typescript": "typescript",
            "ts": "typescript",
            "vue": "vue",
        }
        mapped = alias_map.get(target)
        if mapped is not None:
            processor = self._name_index.get(mapped)
            if processor is not None:
                return processor
        return self._fallback
