from __future__ import annotations

from sari.services.collection.l3_language_processor_registry import L3LanguageProcessorRegistry
from sari.services.collection.l3_language_processor import L3LowConfidenceContext


def _ctx(path: str, count: int = 2) -> L3LowConfidenceContext:
    return L3LowConfidenceContext(
        relative_path=path,
        content_text="import x\nclass A {}\n",
        symbol_count=count,
        has_import_like=True,
        has_cross_file_hint=True,
    )


def test_registry_resolves_vue_processor_first() -> None:
    registry = L3LanguageProcessorRegistry()

    processor = registry.resolve(relative_path="src/App.vue")

    assert processor.name == "vue"
    assert processor.pattern_key(relative_path="src/App.vue") == "ts"


def test_registry_resolves_javascript_alias_extensions() -> None:
    registry = L3LanguageProcessorRegistry()

    processor = registry.resolve(relative_path="src/main.mjs")

    assert processor.name == "javascript"
    assert processor.pattern_key(relative_path="src/main.mjs") == "javascript"


def test_registry_returns_default_for_unsupported_language() -> None:
    registry = L3LanguageProcessorRegistry()

    processor = registry.resolve(relative_path="src/file.rs")

    assert processor.name == "default"
    assert processor.pattern_key(relative_path="src/file.rs") is None
    assert processor.should_route_to_l5(context=_ctx("src/file.rs")) is True


def test_registry_resolves_kotlin_extension() -> None:
    registry = L3LanguageProcessorRegistry()

    processor = registry.resolve(relative_path="app/src/main/kotlin/App.kt")

    assert processor.name == "kotlin"
    assert processor.pattern_key(relative_path="app/src/main/kotlin/App.kt") == "kotlin"


def test_registry_resolves_scala_extension() -> None:
    registry = L3LanguageProcessorRegistry()

    processor = registry.resolve(relative_path="modules/core/src/main/scala/com/acme/App.scala")

    assert processor.name == "scala"
    assert processor.pattern_key(relative_path="modules/core/src/main/scala/com/acme/App.scala") == "scala"
