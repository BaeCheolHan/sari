from __future__ import annotations

from sari.services.collection.l3_treesitter_preprocess_service import (
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
