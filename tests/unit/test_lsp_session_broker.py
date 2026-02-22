from __future__ import annotations

import pytest
from solidlsp.ls_config import Language

from sari.services.collection.lsp_session_broker import (
    LspBrokerLanguageProfile,
    LspSessionBroker,
)


def _profiles() -> dict[str, LspBrokerLanguageProfile]:
    return {
        "java": LspBrokerLanguageProfile(
            language="java",
            hot_lanes=1,
            backlog_lanes=1,
            sticky_idle_ttl_sec=600.0,
            switch_cooldown_sec=5.0,
            min_lease_ms=1000,
            shared_budget_group=None,
        ),
        "typescript": LspBrokerLanguageProfile(
            language="typescript",
            hot_lanes=1,
            backlog_lanes=1,
            sticky_idle_ttl_sec=180.0,
            switch_cooldown_sec=2.0,
            min_lease_ms=200,
            shared_budget_group="ts-vue",
        ),
        "vue": LspBrokerLanguageProfile(
            language="vue",
            hot_lanes=1,
            backlog_lanes=1,
            sticky_idle_ttl_sec=240.0,
            switch_cooldown_sec=2.0,
            min_lease_ms=200,
            shared_budget_group="ts-vue",
        ),
    }


def _new_broker(
    now_ref: dict[str, float] | None = None,
    *,
    backlog_min_share: float = 0.0,
    optional_scaffolding_enabled: bool = False,
) -> LspSessionBroker:
    if now_ref is None:
        now_ref = {"t": 100.0}
    return LspSessionBroker(
        profiles=_profiles(),
        max_standby_sessions_per_lang=2,
        max_standby_sessions_per_budget_group=2,
        backlog_min_share=backlog_min_share,
        optional_scaffolding_enabled=optional_scaffolding_enabled,
        now_monotonic=lambda: now_ref["t"],
    )


def test_broker_shared_budget_group_caps_ts_vue_total_active_sessions() -> None:
    now = 100.0
    broker = _new_broker({"t": now})
    broker.set_budget_group_active_cap("ts-vue", 1)

    ts_scope = "/workspace/apps/web"
    vue_scope = "/workspace/apps/admin"

    lease1 = broker.acquire_lease(
        language=Language.TYPESCRIPT,
        lsp_scope_root=ts_scope,
        lane="hot",
        hotness_score=10.0,
        pending_jobs_in_scope=10,
    )
    assert lease1.granted is True
    assert lease1.reason in {"admitted", "active_reuse"}

    lease2 = broker.acquire_lease(
        language=Language.VUE,
        lsp_scope_root=vue_scope,
        lane="hot",
        hotness_score=11.0,
        pending_jobs_in_scope=5,
    )
    assert lease2.granted is False
    assert lease2.reason == "budget_group_blocked"

    broker.release_lease(lease1)


def test_broker_lease_context_manager_releases_on_exception() -> None:
    broker = _new_broker({"t": 100.0})
    with pytest.raises(RuntimeError):
        with broker.lease(
            language=Language.JAVA,
            lsp_scope_root="/workspace/apps/api",
            lane="backlog",
            hotness_score=1.0,
            pending_jobs_in_scope=100,
        ) as lease:
            assert lease.granted is True
            raise RuntimeError("boom")


def test_broker_lease_context_manager_releases_on_exception_post_state() -> None:
    broker = _new_broker({"t": 100.0})
    try:
        with broker.lease(
            language=Language.JAVA,
            lsp_scope_root="/workspace/apps/api",
            lane="backlog",
            hotness_score=1.0,
            pending_jobs_in_scope=100,
        ):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    snap = broker.get_snapshot()
    assert snap.active_sessions_by_language.get("java", 0) == 0


def test_broker_min_lease_blocks_immediate_preemption() -> None:
    now_ref = {"t": 100.0}
    broker = _new_broker(now_ref)
    broker.set_language_active_cap("java", lane="hot", cap=1)

    lease1 = broker.acquire_lease(
        language=Language.JAVA,
        lsp_scope_root="/workspace/repoA",
        lane="hot",
        hotness_score=10.0,
        pending_jobs_in_scope=1,
    )
    assert lease1.granted is True

    lease2 = broker.acquire_lease(
        language=Language.JAVA,
        lsp_scope_root="/workspace/repoB",
        lane="hot",
        hotness_score=999.0,
        pending_jobs_in_scope=1,
    )
    assert lease2.granted is False
    assert lease2.reason in {"min_lease", "cooldown", "budget_blocked"}

    now_ref["t"] += 2.0
    broker.release_lease(lease1)


def test_broker_backlog_min_share_blocks_hot_after_repeated_hot_grants_when_backlog_waits() -> None:
    now_ref = {"t": 100.0}
    broker = _new_broker(now_ref, backlog_min_share=0.2)
    broker.set_budget_group_active_cap("ts-vue", 1)

    # hot lane repeatedly acquires/releases first (no backlog demand yet)
    for _ in range(4):
        lease = broker.acquire_lease(
            language=Language.TYPESCRIPT,
            lsp_scope_root="/workspace/hot",
            lane="hot",
            hotness_score=10.0,
            pending_jobs_in_scope=10,
        )
        assert lease.granted is True
        broker.release_lease(lease)
        now_ref["t"] += 0.1

    # backlog lane attempts and gets blocked by budget cap -> backlog demand is recorded
    hot_hold = broker.acquire_lease(
        language=Language.TYPESCRIPT,
        lsp_scope_root="/workspace/hot",
        lane="hot",
        hotness_score=10.0,
        pending_jobs_in_scope=10,
    )
    assert hot_hold.granted is True
    backlog = broker.acquire_lease(
        language=Language.VUE,
        lsp_scope_root="/workspace/backlog",
        lane="backlog",
        hotness_score=1.0,
        pending_jobs_in_scope=100,
    )
    assert backlog.granted is False
    assert backlog.reason in {"budget_group_blocked", "budget_blocked"}
    broker.release_lease(hot_hold)

    # with backlog demand pending and hot streak already high, another hot grant is throttled
    hot_again = broker.acquire_lease(
        language=Language.TYPESCRIPT,
        lsp_scope_root="/workspace/hot",
        lane="hot",
        hotness_score=10.0,
        pending_jobs_in_scope=10,
    )
    assert hot_again.granted is False
    assert hot_again.reason == "starvation_guard"


def test_broker_backlog_min_share_clears_guard_after_backlog_grant() -> None:
    now_ref = {"t": 100.0}
    broker = _new_broker(now_ref, backlog_min_share=0.2)
    broker.set_budget_group_active_cap("ts-vue", 1)

    # Build hot streak and record backlog demand.
    for _ in range(4):
        lease = broker.acquire_lease(
            language=Language.TYPESCRIPT,
            lsp_scope_root="/workspace/hot",
            lane="hot",
            hotness_score=10.0,
            pending_jobs_in_scope=10,
        )
        assert lease.granted is True
        broker.release_lease(lease)
    hot_hold = broker.acquire_lease(
        language=Language.TYPESCRIPT,
        lsp_scope_root="/workspace/hot",
        lane="hot",
        hotness_score=10.0,
        pending_jobs_in_scope=10,
    )
    assert hot_hold.granted is True
    blocked_backlog = broker.acquire_lease(
        language=Language.VUE,
        lsp_scope_root="/workspace/backlog",
        lane="backlog",
        hotness_score=1.0,
        pending_jobs_in_scope=100,
    )
    assert blocked_backlog.granted is False
    broker.release_lease(hot_hold)

    # backlog gets a turn -> guard clears
    backlog_ok = broker.acquire_lease(
        language=Language.VUE,
        lsp_scope_root="/workspace/backlog",
        lane="backlog",
        hotness_score=1.0,
        pending_jobs_in_scope=100,
    )
    assert backlog_ok.granted is True
    broker.release_lease(backlog_ok)

    hot_ok = broker.acquire_lease(
        language=Language.TYPESCRIPT,
        lsp_scope_root="/workspace/hot",
        lane="hot",
        hotness_score=10.0,
        pending_jobs_in_scope=10,
    )
    assert hot_ok.granted is True


def test_broker_optional_scaffolding_off_keeps_behavior_identical() -> None:
    now_ref_off = {"t": 100.0}
    now_ref_on = {"t": 100.0}
    broker_off = _new_broker(now_ref_off, backlog_min_share=0.2, optional_scaffolding_enabled=False)
    broker_on = _new_broker(now_ref_on, backlog_min_share=0.2, optional_scaffolding_enabled=True)
    broker_off.set_budget_group_active_cap("ts-vue", 1)
    broker_on.set_budget_group_active_cap("ts-vue", 1)

    scenarios = [
        (Language.TYPESCRIPT, "/workspace/hot", "hot", 10.0, 10),
        (Language.TYPESCRIPT, "/workspace/hot", "hot", 10.0, 10),
        (Language.VUE, "/workspace/backlog", "backlog", 1.0, 100),
        (Language.TYPESCRIPT, "/workspace/hot2", "hot", 9.0, 5),
    ]

    results_off: list[tuple[bool, str]] = []
    results_on: list[tuple[bool, str]] = []
    for language, scope, lane, hotness, pending in scenarios:
        lease_off = broker_off.acquire_lease(
            language=language,
            lsp_scope_root=scope,
            lane=lane,
            hotness_score=hotness,
            pending_jobs_in_scope=pending,
        )
        lease_on = broker_on.acquire_lease(
            language=language,
            lsp_scope_root=scope,
            lane=lane,
            hotness_score=hotness,
            pending_jobs_in_scope=pending,
        )
        results_off.append((lease_off.granted, lease_off.reason))
        results_on.append((lease_on.granted, lease_on.reason))
        if lease_off.granted:
            broker_off.release_lease(lease_off)
        if lease_on.granted:
            broker_on.release_lease(lease_on)
        now_ref_off["t"] += 0.1
        now_ref_on["t"] += 0.1

    assert results_off == results_on


def test_broker_optional_scaffolding_emits_metrics_only_when_enabled() -> None:
    broker = _new_broker({"t": 100.0}, optional_scaffolding_enabled=True)
    lease = broker.acquire_lease(
        language=Language.JAVA,
        lsp_scope_root="/workspace/repo",
        lane="hot",
        hotness_score=5.0,
        pending_jobs_in_scope=3,
    )
    assert lease.granted is True
    broker.release_lease(lease)

    metrics = broker.get_metrics()
    assert "broker_optional_cost_obs_total" in metrics
    assert "broker_optional_drr_obs_total" in metrics
    assert "broker_optional_cost_class_s" in metrics or "broker_optional_cost_class_m" in metrics or "broker_optional_cost_class_l" in metrics
    assert any(key.startswith("broker_optional_cost_estimate_total_") for key in metrics)
    assert any(key.startswith("broker_optional_cost_latency_ms_total_") for key in metrics)

    broker_off = _new_broker({"t": 100.0}, optional_scaffolding_enabled=False)
    lease_off = broker_off.acquire_lease(
        language=Language.JAVA,
        lsp_scope_root="/workspace/repo",
        lane="hot",
        hotness_score=5.0,
        pending_jobs_in_scope=3,
    )
    assert lease_off.granted is True
    broker_off.release_lease(lease_off)
    metrics_off = broker_off.get_metrics()
    assert "broker_optional_cost_obs_total" not in metrics_off
    assert "broker_optional_drr_obs_total" not in metrics_off


def test_broker_metrics_include_lane_switch_defer_and_reuse_counters() -> None:
    now_ref = {"t": 100.0}
    broker = _new_broker(now_ref, backlog_min_share=0.2)
    broker.set_budget_group_active_cap("ts-vue", 1)

    # initial admit
    lease1 = broker.acquire_lease(
        language=Language.TYPESCRIPT,
        lsp_scope_root="/workspace/a",
        lane="hot",
        hotness_score=10.0,
        pending_jobs_in_scope=5,
    )
    assert lease1.granted is True

    # same scope reuse
    lease2 = broker.acquire_lease(
        language=Language.TYPESCRIPT,
        lsp_scope_root="/workspace/a",
        lane="hot",
        hotness_score=9.0,
        pending_jobs_in_scope=5,
    )
    assert lease2.granted is True
    assert lease2.reason == "active_reuse"

    # defer by shared budget group
    blocked = broker.acquire_lease(
        language=Language.VUE,
        lsp_scope_root="/workspace/b",
        lane="backlog",
        hotness_score=1.0,
        pending_jobs_in_scope=100,
    )
    assert blocked.granted is False
    assert blocked.reason in {"budget_group_blocked", "budget_blocked"}

    # release and force a lane switch on typescript hot lane
    broker.release_lease(lease1)
    broker.release_lease(lease2)
    now_ref["t"] += 3.0  # switch cooldown 초과
    lease3 = broker.acquire_lease(
        language=Language.TYPESCRIPT,
        lsp_scope_root="/workspace/c",
        lane="hot",
        hotness_score=8.0,
        pending_jobs_in_scope=3,
    )
    assert lease3.granted is True
    broker.release_lease(lease3)

    metrics = broker.get_metrics()
    assert metrics.get("broker_same_scope_reuse_count_typescript", 0) >= 1
    assert metrics.get("broker_defer_count_vue", 0) >= 1
    assert metrics.get("broker_active_budget_group_ts-vue", 0) == 0
    # initial assign + switch 중 최소 1회는 lane switch로 기록돼야 한다.
    assert metrics.get("broker_lane_switch_count_typescript_hot", 0) >= 1
