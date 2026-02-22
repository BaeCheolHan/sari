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


def test_broker_shared_budget_group_caps_ts_vue_total_active_sessions() -> None:
    now = 100.0
    broker = LspSessionBroker(
        profiles=_profiles(),
        max_standby_sessions_per_lang=2,
        max_standby_sessions_per_budget_group=2,
        now_monotonic=lambda: now,
    )
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
    broker = LspSessionBroker(
        profiles=_profiles(),
        max_standby_sessions_per_lang=2,
        max_standby_sessions_per_budget_group=2,
        now_monotonic=lambda: 100.0,
    )
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
    broker = LspSessionBroker(
        profiles=_profiles(),
        max_standby_sessions_per_lang=2,
        max_standby_sessions_per_budget_group=2,
        now_monotonic=lambda: 100.0,
    )
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
    broker = LspSessionBroker(
        profiles=_profiles(),
        max_standby_sessions_per_lang=2,
        max_standby_sessions_per_budget_group=2,
        now_monotonic=lambda: now_ref["t"],
    )
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

