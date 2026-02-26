"""L3 degraded cheap fallback 계약을 검증한다."""

from __future__ import annotations

import builtins

from sari.services.collection.l3.l3_degraded_fallback_service import L3DegradedFallbackService
from sari.services.collection.l3.l3_treesitter_preprocess_service import L3PreprocessDecision


def test_degraded_fallback_extracts_header_level_symbols_with_regex_only() -> None:
    service = L3DegradedFallbackService()
    content = "\n".join(
        [
            "import os",
            "class Alpha:",
            "    pass",
            "",
            "def beta(x):",
            "    return x",
            "",
            "value = beta(1)",
        ]
    )

    result = service.fallback(relative_path="src/a.py", content_text=content)

    assert result.degraded is True
    assert result.decision is L3PreprocessDecision.L3_ONLY
    assert result.source == "regex_fallback"
    assert [item["name"] for item in result.symbols] == ["Alpha", "beta"]
    assert [item["kind"] for item in result.symbols] == ["class", "function"]


def test_degraded_fallback_unsupported_preset_returns_empty_symbols() -> None:
    service = L3DegradedFallbackService()
    content = "module Foo\n  def bar; end\nend\n"

    result = service.fallback(relative_path="src/a.rb", content_text=content)

    assert result.degraded is True
    assert result.decision is L3PreprocessDecision.L3_ONLY
    assert result.source == "regex_fallback"
    assert result.reason == "l3_degraded_fallback_unsupported_preset"
    assert result.symbols == []


def test_degraded_fallback_ts_preset_handles_tsx_extension() -> None:
    service = L3DegradedFallbackService()
    content = "\n".join(
        [
            "export class ViewModel {}",
            "export async function loadData() { return 1; }",
        ]
    )

    result = service.fallback(relative_path="src/a.tsx", content_text=content)

    assert result.degraded is True
    assert result.decision is L3PreprocessDecision.L3_ONLY
    assert [item["name"] for item in result.symbols] == ["ViewModel", "loadData"]


def test_degraded_fallback_does_not_import_or_call_tree_sitter(monkeypatch) -> None:
    service = L3DegradedFallbackService()
    original_import = builtins.__import__

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if str(name).startswith("tree_sitter"):
            raise AssertionError("tree_sitter should not be imported in degraded fallback")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)
    result = service.fallback(relative_path="src/a.py", content_text="class Alpha:\n    pass\n")
    assert result.source == "regex_fallback"
