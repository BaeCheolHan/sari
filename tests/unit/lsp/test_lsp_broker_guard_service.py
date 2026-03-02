from __future__ import annotations

from contextlib import contextmanager

from solidlsp.ls_config import Language

from sari.services.collection.l5.lsp.broker_guard_service import LspBrokerGuardService


class _Lease:
    def __init__(self, granted: bool, reason: str = "") -> None:
        self.granted = granted
        self.reason = reason


class _Broker:
    def __init__(self, granted: bool = True) -> None:
        self._granted = granted
        self.lease_calls: list[dict[str, object]] = []

    @contextmanager
    def lease(self, **kwargs):  # noqa: ANN003
        self.lease_calls.append(dict(kwargs))
        yield _Lease(self._granted, "budget")

    def is_profiled_language(self, language: Language) -> bool:
        return language is Language.PYTHON


class _Hub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get_or_start(self, *, language: Language, repo_root: str, request_kind: str):
        self.calls.append((language.value, repo_root, request_kind))
        return {"ok": True}


class _Tracer:
    @contextmanager
    def span(self, *args, **kwargs):  # noqa: ANN002, ANN003
        yield


def test_get_or_start_uses_broker_lease_when_enabled_and_profiled() -> None:
    hub = _Hub()
    broker = _Broker(granted=True)
    rejects: list[int] = []
    touched: list[tuple[str, str, str, float]] = []

    service = LspBrokerGuardService(
        hub=hub,
        perf_tracer=_Tracer(),
        get_session_broker=lambda: broker,
        is_session_broker_enabled=lambda: True,
        get_watcher_hotness_tracker=lambda: None,
        increment_broker_guard_reject=lambda: rejects.append(1),
        apply_standby_retention_touch=lambda **kwargs: touched.append((kwargs["language"].value, kwargs["runtime_scope_root"], kwargs["lane"], kwargs["hotness_score"])),
    )

    lsp = service.get_or_start_with_broker_guard(
        language=Language.PYTHON,
        runtime_scope_root="/repo",
        lane="hot",
        pending_jobs_in_scope=1,
        request_kind="indexing",
    )

    assert lsp == {"ok": True}
    assert len(broker.lease_calls) == 1
    assert rejects == []
    assert touched[0][2] == "hot"


def test_get_or_start_raises_when_lease_rejected() -> None:
    hub = _Hub()
    broker = _Broker(granted=False)
    reject_count = {"v": 0}

    service = LspBrokerGuardService(
        hub=hub,
        perf_tracer=_Tracer(),
        get_session_broker=lambda: broker,
        is_session_broker_enabled=lambda: True,
        get_watcher_hotness_tracker=lambda: None,
        increment_broker_guard_reject=lambda: reject_count.__setitem__("v", reject_count["v"] + 1),
        apply_standby_retention_touch=lambda **kwargs: None,
    )

    try:
        service.get_or_start_with_broker_guard(
            language=Language.PYTHON,
            runtime_scope_root="/repo",
            lane="backlog",
            pending_jobs_in_scope=10,
            request_kind="indexing",
        )
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "ERR_LSP_BROKER_LEASE_REQUIRED" in str(exc)

    assert reject_count["v"] == 1
    assert hub.calls == []
