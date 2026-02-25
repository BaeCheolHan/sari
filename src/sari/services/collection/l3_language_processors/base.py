"""Base L3 language processor implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..l3_language_processor import L3LanguageProcessor, L3LowConfidenceContext


@dataclass(frozen=True)
class _BaseConfig:
    name: str
    extensions: tuple[str, ...]
    pattern_key: str | None
    min_symbols_for_l3_only: int = 2
    relax_filename_hints: tuple[str, ...] = (
        "config",
        "settings",
        "option",
        "schema",
        "types",
        "typing",
        "dto",
        "model",
        "interface",
        "constant",
    )
    name_capture_bridge_node_types: tuple[str, ...] = ()


class BaseL3LanguageProcessor(L3LanguageProcessor):
    """Default low-confidence routing behavior shared by languages."""

    def __init__(self, config: _BaseConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return self._config.name

    def supports_path(self, *, relative_path: str) -> bool:
        lowered = relative_path.lower()
        return lowered.endswith(self._config.extensions)

    def pattern_key(self, *, relative_path: str) -> str | None:
        _ = relative_path
        return self._config.pattern_key

    def should_route_to_l5(self, *, context: L3LowConfidenceContext) -> bool:
        min_symbols = self._min_symbols_for_l3_only(relative_path=context.relative_path)
        if context.symbol_count < min_symbols:
            return True
        # Keep this generic cross-file hint guard minimal to avoid LSP over-call.
        if context.symbol_count == 2 and context.has_import_like and context.has_cross_file_hint:
            return True
        return False

    def _min_symbols_for_l3_only(self, *, relative_path: str) -> int:
        lowered = relative_path.lower()
        if lowered.endswith((".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf")):
            return 1
        filename = Path(lowered).name
        for hint in self._config.relax_filename_hints:
            if hint in filename:
                return 1
        return self._config.min_symbols_for_l3_only

    def should_replace_symbol_name(
        self,
        *,
        current_name: str,
        candidate_name: str,
        symbol_kind: str,
        symbol_node_type: str,
        name_parent_node_type: str,
        climb_depth: int,
    ) -> bool:
        _ = (symbol_kind, symbol_node_type, name_parent_node_type, climb_depth)
        current = str(current_name).strip()
        candidate = str(candidate_name).strip()
        if candidate == "":
            return False
        if current == "" or current == "anonymous":
            return True
        if current == candidate:
            return False
        if self._looks_like_symbol_identifier(current):
            return False
        return True

    def allows_name_capture_climb(self, *, parent_node_type: str, climb_depth: int) -> bool:
        _ = climb_depth
        return str(parent_node_type).strip() in self._config.name_capture_bridge_node_types

    def _looks_like_symbol_identifier(self, value: str) -> bool:
        text = str(value).strip()
        if text == "":
            return False
        allowed = set("._$<>[]?,")
        return all(ch.isalnum() or ch in allowed for ch in text)
