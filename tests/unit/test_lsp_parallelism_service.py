from __future__ import annotations

from solidlsp.ls_config import Language

from sari.services.collection.l5.lsp.parallelism_service import LspParallelismService


class _Hub:
    def __init__(self, running: int = 0) -> None:
        self.running = running
        self.pool_calls: list[dict[str, object]] = []
        self.bulk_calls: list[dict[str, object]] = []

    def get_running_instance_count(self, *, language: Language, repo_root: str) -> int:
        _ = language, repo_root
        return self.running

    def acquire_pool(self, **kwargs):  # noqa: ANN003
        self.pool_calls.append(dict(kwargs))
        desired = int(kwargs["desired"])
        return [object() for _ in range(desired)]

    def set_bulk_mode(self, **kwargs):  # noqa: ANN003
        self.bulk_calls.append(dict(kwargs))


def test_parallelism_returns_1_for_profiled_language() -> None:
    hub = _Hub(running=3)
    skips = {"v": 0}
    service = LspParallelismService(
        hub=hub,
        is_profiled_language=lambda lang: True,
        ensure_prewarm=lambda **kwargs: None,
        increment_broker_parallelism_guard_skip=lambda: skips.__setitem__("v", skips["v"] + 1),
    )

    out = service.get_parallelism(repo_root="/repo", language=Language.PYTHON)

    assert out == 1
    assert skips["v"] == 1


def test_parallelism_uses_running_or_prewarm() -> None:
    hub = _Hub(running=0)
    calls = {"prewarm": 0}
    service = LspParallelismService(
        hub=hub,
        is_profiled_language=lambda lang: False,
        ensure_prewarm=lambda **kwargs: calls.__setitem__("prewarm", calls["prewarm"] + 1),
        increment_broker_parallelism_guard_skip=lambda: None,
    )

    out = service.get_parallelism(repo_root="/repo", language=Language.PYTHON)

    assert out == 1
    assert calls["prewarm"] == 1


def test_parallelism_for_batch_uses_acquire_pool() -> None:
    hub = _Hub(running=0)
    service = LspParallelismService(
        hub=hub,
        is_profiled_language=lambda lang: False,
        ensure_prewarm=lambda **kwargs: None,
        increment_broker_parallelism_guard_skip=lambda: None,
    )

    out = service.get_parallelism_for_batch(repo_root="/repo", language=Language.PYTHON, batch_size=4)

    assert out == 4
    assert len(hub.pool_calls) == 1


def test_set_bulk_mode_delegates_when_not_profiled() -> None:
    hub = _Hub()
    service = LspParallelismService(
        hub=hub,
        is_profiled_language=lambda lang: False,
        ensure_prewarm=lambda **kwargs: None,
        increment_broker_parallelism_guard_skip=lambda: None,
    )

    service.set_bulk_mode(repo_root="/repo", language=Language.PYTHON, enabled=True)

    assert len(hub.bulk_calls) == 1
    assert hub.bulk_calls[0]["enabled"] is True
