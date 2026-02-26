"""Broker standby retention touch/prune 처리 서비스."""

from __future__ import annotations

from solidlsp.ls_config import Language


class LspStandbyRetentionService:
    """hot lane에서 standby retention touch/prune 정책을 적용한다."""

    def __init__(self, *, get_hub) -> None:
        self._get_hub = get_hub

    def apply(
        self,
        *,
        language: Language,
        runtime_scope_root: str,
        lane: str,
        hotness_score: float,
        session_broker: object | None,
        session_broker_enabled: bool,
    ) -> None:
        if lane != "hot":
            return
        if not session_broker_enabled or session_broker is None or not session_broker.is_profiled_language(language):
            return
        hub = self._get_hub()
        plan_fn = getattr(session_broker, "get_standby_retention_plan", None)
        touch_fn = getattr(hub, "touch", None)
        prune_fn = getattr(hub, "prune_retention", None)
        if not callable(plan_fn) or not callable(touch_fn):
            return
        try:
            ttl_override_sec, keep_scopes = plan_fn(
                language=language,
                requested_ttl_sec=60.0,
            )
            if runtime_scope_root in keep_scopes and float(ttl_override_sec) > 0.0:
                touch_fn(
                    language=language,
                    repo_root=runtime_scope_root,
                    ttl_override_sec=float(ttl_override_sec),
                    retention_tier="standby",
                    hotness_score=float(hotness_score),
                )
            if callable(prune_fn):
                prune_fn(language=language, keep_repo_roots=set(keep_scopes), retention_tier="standby")
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
            return
