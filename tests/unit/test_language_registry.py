"""운영 언어 레지스트리 정책을 검증한다."""

from sari.core.language_registry import (
    get_default_collection_extensions,
    get_enabled_language_names,
    resolve_language_from_path,
)
from solidlsp.ls_config import Language


def test_language_registry_enables_many_languages() -> None:
    """활성 언어 수는 대규모 지원 기준을 충족해야 한다."""
    names = get_enabled_language_names()
    assert len(names) >= 35


def test_language_registry_resolves_multi_language_extensions() -> None:
    """핵심 언어 외 확장자도 정확히 매핑되어야 한다."""
    assert resolve_language_from_path("a.cpp") == Language.CPP
    assert resolve_language_from_path("a.cs") == Language.CSHARP
    assert resolve_language_from_path("a.swift") == Language.SWIFT
    assert resolve_language_from_path("a.php") == Language.PHP
    assert resolve_language_from_path("a.vue") == Language.VUE
    assert resolve_language_from_path("a.toml") == Language.TOML


def test_language_registry_default_extensions_cover_core_stack() -> None:
    """기본 수집 확장자에는 다언어 핵심 스택이 포함되어야 한다."""
    include_ext = set(get_default_collection_extensions())
    assert ".py" in include_ext
    assert ".ts" in include_ext
    assert ".java" in include_ext
    assert ".kt" in include_ext
    assert ".go" in include_ext
    assert ".rs" in include_ext
    assert ".cpp" in include_ext
    assert ".cs" in include_ext
