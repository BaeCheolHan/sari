"""MCP 도구 인자 정규화를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


ARG_META_KEY = "__sari_arg_meta"


@dataclass(frozen=True)
class ArgAliasRuleDTO:
    """별칭 규칙을 표현한다."""

    canonical: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedArgumentsDTO:
    """정규화 결과를 표현한다."""

    arguments: dict[str, object]
    received_keys: list[str]
    normalized_from: dict[str, str]


_READ_ALIAS_RULES: tuple[ArgAliasRuleDTO, ...] = (
    ArgAliasRuleDTO(canonical="target", aliases=("path", "file_path", "relative_path")),
)

_SEARCH_ALIAS_RULES: tuple[ArgAliasRuleDTO, ...] = (
    ArgAliasRuleDTO(canonical="query", aliases=("q", "keyword")),
)

_SYMBOL_ALIAS_RULES: tuple[ArgAliasRuleDTO, ...] = (
    ArgAliasRuleDTO(canonical="symbol", aliases=("symbol_id", "sid", "name", "target")),
)

_SEARCH_SYMBOL_ALIAS_RULES: tuple[ArgAliasRuleDTO, ...] = (
    ArgAliasRuleDTO(canonical="path_prefix", aliases=("path",)),
)

_TOOL_RULES: dict[str, tuple[ArgAliasRuleDTO, ...]] = {
    "search": _SEARCH_ALIAS_RULES,
    "read": _READ_ALIAS_RULES,
    "read_symbol": _SYMBOL_ALIAS_RULES,
    "get_callers": _SYMBOL_ALIAS_RULES,
    "get_implementations": _SYMBOL_ALIAS_RULES,
    "call_graph": _SYMBOL_ALIAS_RULES,
    "search_symbol": _SEARCH_ALIAS_RULES + _SEARCH_SYMBOL_ALIAS_RULES,
    "knowledge": _SEARCH_ALIAS_RULES,
    "get_context": _SEARCH_ALIAS_RULES,
    "get_snippet": _SEARCH_ALIAS_RULES,
    "list_symbols": _SEARCH_ALIAS_RULES,
}

_READ_MODE_ALIASES: dict[str, str] = {
    "file_preview": "file",
    "preview": "diff_preview",
}


def normalize_tool_arguments(tool_name: str, arguments: Mapping[str, object]) -> NormalizedArgumentsDTO:
    """도구 인자를 canonical 형태로 정규화한다."""
    normalized = dict(arguments)
    received_keys = [str(key) for key in arguments.keys()]
    normalized_from: dict[str, str] = {}
    rules = _TOOL_RULES.get(tool_name, ())

    for rule in rules:
        canonical_value = normalized.get(rule.canonical)
        if _has_value(canonical_value):
            continue
        for alias in rule.aliases:
            alias_value = normalized.get(alias)
            if not _has_value(alias_value):
                continue
            normalized[rule.canonical] = alias_value
            normalized_from[rule.canonical] = alias
            break

    if tool_name == "read":
        mode_raw = normalized.get("mode")
        if isinstance(mode_raw, str) and mode_raw.strip() != "":
            mode_lower = mode_raw.strip().lower()
            mapped_mode = _READ_MODE_ALIASES.get(mode_lower)
            if mapped_mode is not None:
                normalized["mode"] = mapped_mode
                normalized_from["mode"] = mode_raw

    return NormalizedArgumentsDTO(
        arguments=_attach_meta(normalized, received_keys=received_keys, normalized_from=normalized_from),
        received_keys=received_keys,
        normalized_from=normalized_from,
    )


def _attach_meta(
    arguments: dict[str, object],
    *,
    received_keys: list[str],
    normalized_from: dict[str, str],
) -> dict[str, object]:
    """인자 메타를 내부 필드로 부착한다."""
    payload = dict(arguments)
    payload[ARG_META_KEY] = {
        "received_keys": list(received_keys),
        "normalized_from": dict(normalized_from),
    }
    return payload


def _has_value(value: object) -> bool:
    """값 유효성을 판정한다."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True
