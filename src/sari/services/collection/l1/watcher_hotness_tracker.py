"""watchdog/interactive 신호 기반 hotness 추적기 (Phase 1 Baseline)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import threading
import time
from typing import Callable

from solidlsp.ls_config import Language


@dataclass(frozen=True)
class _TimedWeight:
    at_monotonic: float
    weight: float


class WatcherHotnessTracker:
    """cheap signal 기반 scope hotness를 계산한다.

    Phase 1 Baseline:
    - watcher event / interactive signal 기록
    - decay/prune
    - deleted/moved 시 scope cache invalidation signal 전달
    - 실제 scheduling behavior 변경은 PR2 baseline에서 강제하지 않음 (관측/기반 목적)
    """

    def __init__(
        self,
        *,
        event_window_sec: float = 10.0,
        decay_window_sec: float = 30.0,
        now_monotonic: Callable[[], float] | None = None,
        scope_cache_invalidator: Callable[[str, str], None] | None = None,
    ) -> None:
        self._event_window_sec = max(1.0, float(event_window_sec))
        self._decay_window_sec = max(self._event_window_sec, float(decay_window_sec))
        self._now_monotonic = now_monotonic or time.monotonic
        self._scope_cache_invalidator = scope_cache_invalidator
        self._lock = threading.Lock()
        self._signals: dict[tuple[str, str], list[_TimedWeight]] = defaultdict(list)

    def record_fs_event(
        self,
        *,
        event_type: str,
        repo_root: str,
        relative_path: str,
        language: Language | None,
        lsp_scope_root: str | None,
    ) -> None:
        et = event_type.strip().lower()
        if et in {"deleted", "moved"} and self._scope_cache_invalidator is not None:
            try:
                self._scope_cache_invalidator(repo_root, relative_path)
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                # hotness path는 best-effort
                ...
        if language is None or not isinstance(lsp_scope_root, str) or lsp_scope_root.strip() == "":
            return
        weight = 1.0
        if et == "created":
            weight = 1.25
        elif et == "moved":
            weight = 1.5
        self._record_signal(language=language, lsp_scope_root=lsp_scope_root, weight=weight)

    def record_interactive_signal(
        self,
        *,
        signal_kind: str,
        language: Language | None,
        lsp_scope_root: str | None,
        weight: float = 2.0,
    ) -> None:
        del signal_kind
        if language is None or not isinstance(lsp_scope_root, str) or lsp_scope_root.strip() == "":
            return
        self._record_signal(language=language, lsp_scope_root=lsp_scope_root, weight=max(0.0, float(weight)))

    def record_backlog_signal(
        self,
        *,
        language: Language | None,
        lsp_scope_root: str | None,
        pending_jobs_in_scope: int,
        cost_weight: float = 0.0,
    ) -> float:
        """Batch Affinity 관측용 score 계산 (Phase 1 Baseline: behavior 미적용)."""
        if language is None or not isinstance(lsp_scope_root, str) or lsp_scope_root.strip() == "":
            return 0.0
        backlog_bonus = max(0.0, float(pending_jobs_in_scope)) * max(0.0, float(cost_weight))
        if backlog_bonus > 0:
            self._record_signal(language=language, lsp_scope_root=lsp_scope_root, weight=backlog_bonus)
        return self.get_scope_hotness(language=language, lsp_scope_root=lsp_scope_root)

    def get_scope_hotness(self, *, language: Language, lsp_scope_root: str) -> float:
        key = (language.value, lsp_scope_root)
        now = self._now_monotonic()
        with self._lock:
            items = self._signals.get(key, [])
            self._signals[key] = [item for item in items if (now - item.at_monotonic) <= self._decay_window_sec]
            fresh = [item for item in self._signals[key] if (now - item.at_monotonic) <= self._event_window_sec]
            if not self._signals[key]:
                self._signals.pop(key, None)
            return float(sum(item.weight for item in fresh))

    def prune(self) -> None:
        now = self._now_monotonic()
        with self._lock:
            for key in list(self._signals.keys()):
                kept = [item for item in self._signals[key] if (now - item.at_monotonic) <= self._decay_window_sec]
                if kept:
                    self._signals[key] = kept
                else:
                    self._signals.pop(key, None)

    def get_metrics(self) -> dict[str, int]:
        with self._lock:
            return {"hotness_scope_keys": len(self._signals)}

    def _record_signal(self, *, language: Language, lsp_scope_root: str, weight: float) -> None:
        key = (language.value, lsp_scope_root)
        now = self._now_monotonic()
        with self._lock:
            self._signals[key].append(_TimedWeight(at_monotonic=now, weight=weight))
