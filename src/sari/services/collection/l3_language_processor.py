"""L3 language-aware preprocess processor contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class L3LowConfidenceContext:
    """Common low-confidence decision inputs for language processors."""

    relative_path: str
    content_text: str
    symbol_count: int
    has_import_like: bool
    has_cross_file_hint: bool


class L3LanguageProcessor(Protocol):
    """Language-specific preprocess behavior contract."""

    @property
    def name(self) -> str:
        """Return processor canonical language name."""

    def supports_path(self, *, relative_path: str) -> bool:
        """Return whether this processor is selected for path."""

    def pattern_key(self, *, relative_path: str) -> str | None:
        """Return regex/tree-sitter pattern key, or None when unsupported."""

    def should_route_to_l5(self, *, context: L3LowConfidenceContext) -> bool:
        """Return True when low-confidence should route to L5."""

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
        """Return True when a capture name should replace current symbol name."""

    def allows_name_capture_climb(self, *, parent_node_type: str, climb_depth: int) -> bool:
        """Return True when capture-name binding can climb to parent symbol node."""
