"""Broker lease guard 및 LSP get_or_start 경계 서비스."""

from __future__ import annotations

from solidlsp.ls_config import Language


class LspBrokerGuardService:
    """broker lease/guard 흐름을 backend 본체에서 분리한다."""

    def __init__(
        self,
        *,
        hub: object,
        perf_tracer: object,
        get_session_broker,
        is_session_broker_enabled,
        get_watcher_hotness_tracker,
        is_batch_broker_throughput_mode_enabled,
        get_batch_broker_pending_threshold,
        increment_broker_guard_reject,
        apply_standby_retention_touch,
    ) -> None:
        self._hub = hub
        self._perf_tracer = perf_tracer
        self._get_session_broker = get_session_broker
        self._is_session_broker_enabled = is_session_broker_enabled
        self._get_watcher_hotness_tracker = get_watcher_hotness_tracker
        self._is_batch_broker_throughput_mode_enabled = is_batch_broker_throughput_mode_enabled
        self._get_batch_broker_pending_threshold = get_batch_broker_pending_threshold
        self._increment_broker_guard_reject = increment_broker_guard_reject
        self._apply_standby_retention_touch = apply_standby_retention_touch

    def is_profiled_broker_language(self, language: Language) -> bool:
        broker = self._get_session_broker()
        if not self._is_session_broker_enabled() or broker is None:
            return False
        is_profiled = getattr(broker, "is_profiled_language", None)
        if not callable(is_profiled):
            return False
        try:
            return bool(is_profiled(language))
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
            return False

    def get_or_start_with_broker_guard(
        self,
        *,
        language: Language,
        runtime_scope_root: str,
        lane: str,
        pending_jobs_in_scope: int,
        request_kind: str,
        trace_name: str = "extract_once.get_or_start",
        trace_phase: str = "l3_extract",
    ):
        broker = self._get_session_broker()
        if self._is_session_broker_enabled() and broker is not None and self.is_profiled_broker_language(language):
            hotness = 0.0
            tracker = self._get_watcher_hotness_tracker()
            if tracker is not None:
                try:
                    hotness = tracker.get_scope_hotness(language=language, lsp_scope_root=runtime_scope_root)
                except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                    hotness = 0.0
            throughput_mode = (
                self._is_batch_broker_throughput_mode_enabled()
                and lane.lower() == "backlog"
                and int(pending_jobs_in_scope) >= int(self._get_batch_broker_pending_threshold())
            )
            with broker.lease(
                language=language,
                lsp_scope_root=runtime_scope_root,
                lane=lane,
                hotness_score=hotness,
                pending_jobs_in_scope=max(0, int(pending_jobs_in_scope)),
                throughput_mode=throughput_mode,
            ) as lease:
                if not lease.granted:
                    self._increment_broker_guard_reject()
                    raise RuntimeError(
                        "ERR_LSP_BROKER_LEASE_REQUIRED: "
                        f"lang={language.value}, scope={runtime_scope_root}, lane={lane}, reason={lease.reason}"
                    )
                with self._perf_tracer.span(
                    trace_name,
                    phase=trace_phase,
                    repo_root=runtime_scope_root,
                    language=language.value,
                    request_kind=request_kind,
                    lane=lane,
                ):
                    lsp = self._hub.get_or_start(language=language, repo_root=runtime_scope_root, request_kind=request_kind)
                lane_key = lane.lower() if lane.lower() else lane
                self._apply_standby_retention_touch(
                    language=language,
                    runtime_scope_root=runtime_scope_root,
                    lane=lane_key,
                    hotness_score=hotness,
                )
                return lsp

        with self._perf_tracer.span(
            trace_name,
            phase=trace_phase,
            repo_root=runtime_scope_root,
            language=language.value,
            request_kind=request_kind,
            lane=lane,
        ):
            return self._hub.get_or_start(language=language, repo_root=runtime_scope_root, request_kind=request_kind)
