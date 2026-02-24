"""Static registry for L3 language processors."""

from __future__ import annotations

from .l3_language_processor import L3LanguageProcessor
from .l3_language_processors.default_processor import DefaultL3LanguageProcessor
from .l3_language_processors.java_processor import JavaL3LanguageProcessor
from .l3_language_processors.javascript_processor import JavaScriptL3LanguageProcessor
from .l3_language_processors.kotlin_processor import KotlinL3LanguageProcessor
from .l3_language_processors.python_processor import PythonL3LanguageProcessor
from .l3_language_processors.typescript_processor import TypeScriptL3LanguageProcessor
from .l3_language_processors.vue_processor import VueL3LanguageProcessor


class L3LanguageProcessorRegistry:
    """Path-based static processor registry."""

    def __init__(self) -> None:
        self._processors: tuple[L3LanguageProcessor, ...] = (
            VueL3LanguageProcessor(),
            PythonL3LanguageProcessor(),
            JavaL3LanguageProcessor(),
            KotlinL3LanguageProcessor(),
            TypeScriptL3LanguageProcessor(),
            JavaScriptL3LanguageProcessor(),
        )
        self._fallback: L3LanguageProcessor = DefaultL3LanguageProcessor()

    def resolve(self, *, relative_path: str) -> L3LanguageProcessor:
        for processor in self._processors:
            if processor.supports_path(relative_path=relative_path):
                return processor
        return self._fallback
