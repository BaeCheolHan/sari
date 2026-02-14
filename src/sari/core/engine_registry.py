from __future__ import annotations

from importlib.util import find_spec
from typing import Callable, Optional, Protocol, TypeAlias

from sari.core.fallback_governance import note_fallback_event
from sari.core.models import SearchHit, SearchOptions
from sari.core.engine_runtime import EmbeddedEngine, SqliteSearchEngineAdapter

RepoCandidate: TypeAlias = dict[str, object]
EngineMeta: TypeAlias = dict[str, object]
SearchRows: TypeAlias = list[SearchHit]
RootIds: TypeAlias = list[str]


class SearchEngineInterface(Protocol):
    def search(self, opts: SearchOptions) -> tuple[SearchRows, EngineMeta]:
        ...

    def repo_candidates(self, q: str, limit: int = 3, root_ids: Optional[RootIds] = None) -> list[RepoCandidate]:
        ...


class EngineRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[object, object, object], SearchEngineInterface]] = {}

    def register(self, name: str, factory: Callable[[object, object, object], SearchEngineInterface]) -> None:
        self._factories[name] = factory

    def create(self, name: str, db: object, cfg: object = None, roots: object = None) -> SearchEngineInterface:
        if name not in self._factories:
            raise KeyError(f"engine not registered: {name}")
        return self._factories[name](db, cfg, roots)

    def default(self, db: object, cfg: object = None, roots: object = None) -> SearchEngineInterface:
        name = default_engine_name(cfg)
        return self.create(name, db, cfg, roots)


_REGISTRY = EngineRegistry()
_REGISTRY.register("sqlite", lambda db, _cfg, _roots: SqliteSearchEngineAdapter(db))
_REGISTRY.register("embedded", lambda db, cfg, roots: EmbeddedEngine(db, cfg, roots or []))


def get_registry() -> EngineRegistry:
    return _REGISTRY


def default_engine_name(cfg: object = None) -> str:
    # Priority 11: Auto-detect best engine based on installed libs
    HAS_TANTIVY = find_spec("tantivy") is not None

    if cfg is not None:
        raw_mode = getattr(cfg, "engine_mode", "")
        mode = str(raw_mode or "").strip().lower()
        if mode in {"embedded", "sqlite"}:
            # Respect explicit user choice if valid
            if mode == "embedded" and not HAS_TANTIVY:
                note_fallback_event(
                    "engine_embedded_unavailable_fallback",
                    trigger="embedded_requested_without_tantivy",
                    exit_condition="sqlite_engine_selected",
                )
                return "sqlite" # Graceful fallback
            return mode
            
    # Default selection logic: prefer embedded if lib exists
    if HAS_TANTIVY:
        return "embedded"
    note_fallback_event(
        "engine_default_sqlite_fallback",
        trigger="tantivy_not_installed",
        exit_condition="sqlite_engine_selected",
    )
    return "sqlite"


def get_default_engine(db: object, cfg: object = None, roots: object = None) -> SearchEngineInterface:
    return _REGISTRY.default(db, cfg, roots)
