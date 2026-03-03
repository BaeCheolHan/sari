from __future__ import annotations

import threading
from contextlib import contextmanager

from solidlsp.ls_config import Language

from sari.services.collection.l5.lsp.scope_runtime_service import LspScopeRuntimeService


class _Planner:
    class _Res:
        def __init__(self, root: str, strategy: str = "APPLIED") -> None:
            self.lsp_scope_root = root
            self.strategy = strategy

    def __init__(self, root: str, strategy: str = "APPLIED") -> None:
        self._root = root
        self._strategy = strategy

    def resolve(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        return _Planner._Res(self._root, self._strategy)


class _Tracer:
    @contextmanager
    def span(self, *args, **kwargs):  # noqa: ANN002, ANN003
        yield


def test_resolve_scope_prefers_override() -> None:
    service = LspScopeRuntimeService(
        get_scope_override=lambda repo_root, relative_path: ("/override", "repo"),
        to_scope_relative_path_or_fallback=lambda **kwargs: "src/a.py",
        get_lsp_scope_planner=lambda: None,
        is_lsp_scope_planner_enabled=lambda: False,
        get_scope_active_languages=lambda: None,
        perf_tracer=_Tracer(),
        on_scope_override_hit=lambda: None,
        on_scope_planner_applied=lambda: None,
        on_scope_planner_fallback_index_building=lambda: None,
        l3_scope_pending_hints={},
        l3_scope_pending_hint_lock=threading.Lock(),
        normalize_repo_relative_path=lambda p: p,
    )

    root, rel = service.resolve_lsp_runtime_scope(
        repo_root="/workspace",
        normalized_relative_path="src/a.py",
        language=Language.PYTHON,
    )

    assert root == "/override"
    assert rel == "src/a.py"


def test_resolve_scope_planner_applies_runtime_scope() -> None:
    applied = {"count": 0}
    service = LspScopeRuntimeService(
        get_scope_override=lambda repo_root, relative_path: None,
        to_scope_relative_path_or_fallback=lambda **kwargs: "src/a.py",
        get_lsp_scope_planner=lambda: _Planner("/module", strategy="FALLBACK_INDEX_BUILDING"),
        is_lsp_scope_planner_enabled=lambda: True,
        get_scope_active_languages=lambda: None,
        perf_tracer=_Tracer(),
        on_scope_override_hit=lambda: None,
        on_scope_planner_applied=lambda: applied.__setitem__("count", applied["count"] + 1),
        on_scope_planner_fallback_index_building=lambda: None,
        l3_scope_pending_hints={},
        l3_scope_pending_hint_lock=threading.Lock(),
        normalize_repo_relative_path=lambda p: p,
    )

    root, rel = service.resolve_lsp_runtime_scope(
        repo_root="/workspace",
        normalized_relative_path="src/a.py",
        language=Language.PYTHON,
    )

    assert root == "/module"
    assert rel == "src/a.py"
    assert applied["count"] == 1


def test_consume_l3_scope_pending_hint_decrements_and_pops() -> None:
    hints = {(Language.PYTHON.value, "/repo"): 2}
    service = LspScopeRuntimeService(
        get_scope_override=lambda repo_root, relative_path: None,
        to_scope_relative_path_or_fallback=lambda **kwargs: kwargs["normalized_relative_path"],
        get_lsp_scope_planner=lambda: None,
        is_lsp_scope_planner_enabled=lambda: False,
        get_scope_active_languages=lambda: None,
        perf_tracer=_Tracer(),
        on_scope_override_hit=lambda: None,
        on_scope_planner_applied=lambda: None,
        on_scope_planner_fallback_index_building=lambda: None,
        l3_scope_pending_hints=hints,
        l3_scope_pending_hint_lock=threading.Lock(),
        normalize_repo_relative_path=lambda p: p,
    )

    first = service.consume_l3_scope_pending_hint(language=Language.PYTHON, runtime_scope_root="/repo")
    second = service.consume_l3_scope_pending_hint(language=Language.PYTHON, runtime_scope_root="/repo")

    assert first == 2
    assert second == 1
    assert (Language.PYTHON.value, "/repo") not in hints
