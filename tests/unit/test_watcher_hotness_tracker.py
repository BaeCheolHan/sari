from __future__ import annotations

from solidlsp.ls_config import Language

from sari.services.collection.watcher_hotness_tracker import WatcherHotnessTracker


def test_hotness_tracker_records_modified_burst_and_decays() -> None:
    now = 1000.0
    tracker = WatcherHotnessTracker(
        event_window_sec=10.0,
        decay_window_sec=30.0,
        now_monotonic=lambda: now,
    )

    tracker.record_fs_event(
        event_type="modified",
        repo_root="/workspace",
        relative_path="apps/web/src/main.ts",
        language=Language.TYPESCRIPT,
        lsp_scope_root="/workspace/apps/web",
    )
    tracker.record_fs_event(
        event_type="modified",
        repo_root="/workspace",
        relative_path="apps/web/src/util.ts",
        language=Language.TYPESCRIPT,
        lsp_scope_root="/workspace/apps/web",
    )

    score_now = tracker.get_scope_hotness(language=Language.TYPESCRIPT, lsp_scope_root="/workspace/apps/web")
    assert score_now > 0.0

    now += 40.0
    tracker.prune()
    score_later = tracker.get_scope_hotness(language=Language.TYPESCRIPT, lsp_scope_root="/workspace/apps/web")
    assert score_later == 0.0


def test_hotness_tracker_deleted_event_triggers_scope_cache_invalidation_signal() -> None:
    invalidations: list[tuple[str, str]] = []
    tracker = WatcherHotnessTracker(
        now_monotonic=lambda: 1000.0,
        scope_cache_invalidator=lambda repo_root, relative_path: invalidations.append((repo_root, relative_path)),
    )

    tracker.record_fs_event(
        event_type="deleted",
        repo_root="/workspace",
        relative_path="apps/api/src/App.java",
        language=Language.JAVA,
        lsp_scope_root="/workspace/apps/api",
    )

    assert invalidations == [("/workspace", "apps/api/src/App.java")]


def test_hotness_tracker_supports_interactive_signal_without_fs_event() -> None:
    tracker = WatcherHotnessTracker(now_monotonic=lambda: 1000.0)
    tracker.record_interactive_signal(
        signal_kind="search",
        language=Language.VUE,
        lsp_scope_root="/workspace/apps/web",
        weight=3.0,
    )

    score = tracker.get_scope_hotness(language=Language.VUE, lsp_scope_root="/workspace/apps/web")
    assert score >= 3.0

