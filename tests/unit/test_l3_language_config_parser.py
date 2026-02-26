from __future__ import annotations

from solidlsp.ls_config import Language

from sari.services.collection.l3_language_config_parser import (
    parse_l3_supported_languages,
    parse_lsp_probe_l1_languages,
)


def test_parse_lsp_probe_languages_maps_extensions() -> None:
    parsed = parse_lsp_probe_l1_languages(("py", "java", "unknown"))
    assert Language.PYTHON in parsed
    assert Language.JAVA in parsed


def test_parse_l3_supported_languages_uses_aliases_and_fallback() -> None:
    parsed = parse_l3_supported_languages(("py", "js", "kt"))
    assert Language.PYTHON in parsed
    assert Language.TYPESCRIPT in parsed
    assert Language.KOTLIN in parsed
