"""L3 관련 언어 설정 파서."""

from __future__ import annotations

from collections.abc import Iterable

from solidlsp.ls_config import Language

from sari.core.language_registry import get_enabled_language_names, resolve_language_from_path


def parse_lsp_probe_l1_languages(items: Iterable[str]) -> set[Language]:
    parsed: set[Language] = set()
    for item in items:
        raw = item.strip().lower()
        if raw == "":
            continue
        language = resolve_language_from_path(file_path=f"file.{raw}")
        if language is not None:
            parsed.add(language)
    return parsed


def parse_l3_supported_languages(items: Iterable[str]) -> set[Language]:
    parsed: set[Language] = set()
    aliases = {
        "py": Language.PYTHON,
        "js": Language.TYPESCRIPT,
        "ts": Language.TYPESCRIPT,
        "kt": Language.KOTLIN,
        "rs": Language.RUST,
        "cs": Language.CSHARP,
        "rb": Language.RUBY,
    }
    for item in items:
        raw = item.strip().lower()
        if raw == "":
            continue
        if raw in aliases:
            parsed.add(aliases[raw])
            continue
        try:
            parsed.add(Language(raw))
            continue
        except ValueError:
            # enum 변환 실패는 확장자 기반 판별로 fallback한다.
            ...
        language = resolve_language_from_path(file_path=f"file.{raw}")
        if language is not None:
            parsed.add(language)
    if len(parsed) > 0:
        return parsed
    # 잘못된 설정으로 전체가 비활성화되지 않도록 기본값으로 복구한다.
    return {Language(name) for name in get_enabled_language_names()}
