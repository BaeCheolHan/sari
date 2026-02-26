from __future__ import annotations

from solidlsp.ls_config import Language

from sari.services.collection.lsp_standby_retention_service import LspStandbyRetentionService


class _Broker:
    def __init__(self, profiled: bool = True) -> None:
        self.profiled = profiled

    def is_profiled_language(self, language: Language) -> bool:
        _ = language
        return self.profiled

    def get_standby_retention_plan(self, *, language: Language, requested_ttl_sec: float):
        _ = language, requested_ttl_sec
        return (120.0, {"/repo"})


class _Hub:
    def __init__(self) -> None:
        self.touch_calls: list[dict[str, object]] = []
        self.prune_calls: list[dict[str, object]] = []

    def touch(self, **kwargs):  # noqa: ANN003
        self.touch_calls.append(dict(kwargs))

    def prune_retention(self, **kwargs):  # noqa: ANN003
        self.prune_calls.append(dict(kwargs))


def test_apply_touches_and_prunes_for_hot_profiled_language() -> None:
    hub = _Hub()
    service = LspStandbyRetentionService(get_hub=lambda: hub)

    service.apply(
        language=Language.PYTHON,
        runtime_scope_root="/repo",
        lane="hot",
        hotness_score=0.8,
        session_broker=_Broker(profiled=True),
        session_broker_enabled=True,
    )

    assert len(hub.touch_calls) == 1
    assert len(hub.prune_calls) == 1
    assert hub.touch_calls[0]["retention_tier"] == "standby"


def test_apply_noop_when_not_hot_lane() -> None:
    hub = _Hub()
    service = LspStandbyRetentionService(get_hub=lambda: hub)

    service.apply(
        language=Language.PYTHON,
        runtime_scope_root="/repo",
        lane="backlog",
        hotness_score=0.1,
        session_broker=_Broker(profiled=True),
        session_broker_enabled=True,
    )

    assert hub.touch_calls == []
    assert hub.prune_calls == []
