from __future__ import annotations

from sari.services.collection.l3.l3_tree_sitter_outline import TreeSitterOutlineResult
from sari.services.collection.l3.l3_treesitter_preprocess_service import (
    L3PreprocessDecision,
    L3TreeSitterPreprocessService,
)


def test_scala_preprocess_regex_fallback_extracts_symbols_when_tree_sitter_degraded() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = """
package com.acme.core

final class UserService {
  def createUser(name: String): Unit = {}
  val version = "1.0"
}
"""

    result = service.preprocess(relative_path="src/main/scala/com/acme/UserService.scala", content_text=content)

    names = {str(item.get("name")) for item in result.symbols}
    kinds = {str(item.get("kind")) for item in result.symbols}
    assert result.source == "regex_outline"
    assert result.decision in (L3PreprocessDecision.L3_ONLY, L3PreprocessDecision.NEEDS_L5)
    assert "com.acme.core" in names
    assert "UserService" in names
    assert "createUser" in names
    assert "version" in names
    assert {"module", "class", "method", "field"}.issubset(kinds)


def test_scala_preprocess_marks_heavy_file_as_deferred() -> None:
    service = L3TreeSitterPreprocessService(tree_sitter_enabled=False)
    content = "class A {}\n"
    huge = content * 20000

    result = service.preprocess(
        relative_path="src/main/scala/com/acme/Huge.scala",
        content_text=huge,
        max_bytes=1024,
    )

    assert result.degraded is True
    assert result.decision == L3PreprocessDecision.DEFERRED_HEAVY
    assert result.reason == "l3_preprocess_large_file"


def test_preprocess_forwards_incremental_parse_key_to_outline_extractor() -> None:
    class _StubExtractor:
        def __init__(self) -> None:
            self.last_kwargs: dict[str, object] = {}

        def is_available_for(self, lang_key: str) -> bool:
            _ = lang_key
            return True

        def extract_outline(self, **kwargs):  # noqa: ANN003
            self.last_kwargs = dict(kwargs)
            return TreeSitterOutlineResult(
                symbols=[{"name": "A", "kind": "class", "line": 1, "end_line": 1}],
                degraded=False,
            )

    extractor = _StubExtractor()
    service = L3TreeSitterPreprocessService(
        tree_sitter_enabled=True,
        tree_sitter_outline_extractor=extractor,  # type: ignore[arg-type]
    )
    _ = service.preprocess(
        relative_path="src/main/kotlin/A.kt",
        content_text="class A {}",
        repo_root="/tmp/repo",
    )
    assert extractor.last_kwargs.get("parse_key") == "/tmp/repo::src/main/kotlin/A.kt"


def test_preprocess_supports_legacy_outline_extractor_signature_without_parse_key() -> None:
    class _LegacyExtractor:
        def is_available_for(self, lang_key: str) -> bool:
            _ = lang_key
            return True

        def extract_outline(self, *, lang_key: str, content_text: str, budget_sec: float):  # noqa: ANN001
            _ = (lang_key, content_text, budget_sec)
            return TreeSitterOutlineResult(
                symbols=[{"name": "A", "kind": "class", "line": 1, "end_line": 1}],
                degraded=False,
            )

    service = L3TreeSitterPreprocessService(
        tree_sitter_enabled=True,
        tree_sitter_outline_extractor=_LegacyExtractor(),  # type: ignore[arg-type]
    )
    result = service.preprocess(
        relative_path="src/main/java/A.java",
        content_text="class A {}",
        repo_root="/tmp/repo",
    )
    assert result.source == "tree_sitter_outline"
