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

