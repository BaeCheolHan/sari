"""MCP 도구 인자 정규화를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from sari.core.models import ErrorResponseDTO


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


@dataclass(frozen=True)
class ArgumentHintDTO:
    """인자 오류 힌트를 표현한다."""

    expected: list[str]
    received: list[str]
    example: dict[str, object]
    normalized_from: dict[str, str]


class ArgNormalizationError(RuntimeError):
    """정규화 중 명시적 인자 오류를 표현한다."""

    def __init__(self, *, code: str, message: str, hint: ArgumentHintDTO) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_error_dto(self) -> ErrorResponseDTO:
        """도메인 오류 DTO를 반환한다."""
        return ErrorResponseDTO(code=self.code, message=self.message)


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
    "read_file": (ArgAliasRuleDTO(canonical="relative_path", aliases=("path", "file_path", "target")),),
    "index_file": (ArgAliasRuleDTO(canonical="relative_path", aliases=("path", "file_path", "target")),),
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

_INT_KEYS: frozenset[str] = frozenset(
    {"limit", "offset", "start_line", "end_line", "target_files", "l3_p95_threshold_ms", "dead_ratio_threshold_bps", "workers", "alert_window_sec"}
)

_BOOL_KEYS: frozenset[str] = frozenset({"all", "enabled", "per_language_report", "fail_on_unavailable", "strict_all_languages", "strict_symbol_gate"})


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
        alias_values: dict[str, object] = {}
        for alias in rule.aliases:
            alias_value = normalized.get(alias)
            if not _has_value(alias_value):
                continue
            alias_values[alias] = alias_value
        if len(alias_values) == 0:
            continue
        if len(alias_values) > 1:
            distinct_values = {str(value) for value in alias_values.values()}
            if len(distinct_values) > 1:
                first_alias = list(alias_values.keys())[0]
                raise ArgNormalizationError(
                    code="ERR_ARGUMENT_AMBIGUOUS",
                    message=f"ambiguous aliases for {rule.canonical}",
                    hint=ArgumentHintDTO(
                        expected=[rule.canonical],
                        received=received_keys,
                        example={rule.canonical: alias_values[first_alias]},
                        normalized_from={},
                    ),
                )
        chosen_alias = list(alias_values.keys())[0]
        normalized[rule.canonical] = alias_values[chosen_alias]
        normalized_from[rule.canonical] = chosen_alias

    if tool_name == "read":
        mode_raw = normalized.get("mode")
        if isinstance(mode_raw, str) and mode_raw.strip() != "":
            mode_lower = mode_raw.strip().lower()
            mapped_mode = _READ_MODE_ALIASES.get(mode_lower)
            if mapped_mode is not None:
                normalized["mode"] = mapped_mode
                normalized_from["mode"] = mode_raw

    _coerce_types(normalized)

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


def _coerce_types(arguments: dict[str, object]) -> None:
    """입력 문자열을 기본 스칼라 타입으로 변환한다."""
    for key in _INT_KEYS:
        value = arguments.get(key)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped == "":
            continue
        if stripped.lstrip("-").isdigit():
            arguments[key] = int(stripped)
    for key in _BOOL_KEYS:
        value = arguments.get(key)
        if not isinstance(value, str):
            continue
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            arguments[key] = True
        elif lowered in {"0", "false", "no", "off"}:
            arguments[key] = False
