"""L5 admission 정책 계약을 검증한다."""

from __future__ import annotations

from sari.core.models import L5ReasonCode, L5RejectReason, L5RequestMode
from sari.services.collection.l5.l5_admission_policy import (
    L5AdmissionInput,
    L5AdmissionPolicy,
    L5AdmissionPolicyConfig,
    LanguageL5Policy,
    TokenBucket,
    classify_l5_request_mode,
    normalize_workspace_uid,
)


def _bucket(capacity: float = 100.0) -> TokenBucket:
    return TokenBucket(capacity=capacity, refill_per_sec=0.0, tokens=capacity, last_ts=0.0)


def _policy(
    *,
    config: L5AdmissionPolicyConfig | None = None,
    global_bucket: TokenBucket | None = None,
    lang_bucket: TokenBucket | None = None,
    ws_bucket: TokenBucket | None = None,
) -> L5AdmissionPolicy:
    return L5AdmissionPolicy(
        config=config or L5AdmissionPolicyConfig(),
        global_bucket=global_bucket or _bucket(),
        lang_bucket_provider=lambda _lang: lang_bucket or _bucket(),
        workspace_bucket_provider=lambda _ws: ws_bucket or _bucket(),
    )


def test_classify_l5_request_mode_defaults_to_batch_when_ambiguous() -> None:
    assert classify_l5_request_mode(is_user_triggered=False, is_sync_request=False) is L5RequestMode.BATCH
    assert classify_l5_request_mode(is_user_triggered=True, is_sync_request=False) is L5RequestMode.BATCH
    assert classify_l5_request_mode(is_user_triggered=True, is_sync_request=True) is L5RequestMode.INTERACTIVE


def test_normalize_workspace_uid_is_stable() -> None:
    left = normalize_workspace_uid("/repo/a")
    right = normalize_workspace_uid("/repo/a")
    assert left == right
    assert left != normalize_workspace_uid("/repo/b")


def test_l5_admission_rejects_when_reason_is_missing_in_batch() -> None:
    policy = _policy()
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=None,
            mode=L5RequestMode.BATCH,
            workspace_uid="ws",
            total_rate=0.0,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is False
    assert decision.reject_reason is L5RejectReason.REASON_MISSING
    assert decision.reject_stage == "reason"


def test_l5_admission_interactive_reason_missing_is_normalized_with_hardcap() -> None:
    config = L5AdmissionPolicyConfig(
        unknown_reason_fallback_per_10sec_per_workspace_caller_max=1,
    )
    policy = _policy(config=config)
    first = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=None,
            mode=L5RequestMode.INTERACTIVE,
            workspace_uid="ws",
            total_rate=0.0,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=1.0,
            caller="search",
        ),
        language_key="python",
    )
    second = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=None,
            mode=L5RequestMode.INTERACTIVE,
            workspace_uid="ws",
            total_rate=0.0,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=2.0,
            caller="search",
        ),
        language_key="python",
    )
    assert first.admit_l5 is True
    assert first.reason_code is L5ReasonCode.USER_INTERACTIVE_UNKNOWN
    assert second.admit_l5 is False
    assert second.reject_reason is L5RejectReason.REASON_MISSING


def test_l5_admission_rejects_when_policy_disabled() -> None:
    config = L5AdmissionPolicyConfig(
        language_policy_map={
            "python": LanguageL5Policy(enabled=False),
        },
    )
    policy = _policy(config=config)
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.USER_INTERACTIVE,
            mode=L5RequestMode.INTERACTIVE,
            workspace_uid="ws",
            total_rate=0.0,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is False
    assert decision.reject_reason is L5RejectReason.POLICY_DISABLED


def test_l5_admission_rejects_interactive_only_reason_in_batch_mode() -> None:
    policy = _policy()
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.RENAME_DEFINITION_PRECISION,
            mode=L5RequestMode.BATCH,
            workspace_uid="ws",
            total_rate=0.0,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is False
    assert decision.reject_reason is L5RejectReason.MODE_NOT_ALLOWED


def test_l5_admission_reject_precedence_mode_before_rate_and_burst() -> None:
    """문서 순서대로 mode 위반은 rate/burst보다 먼저 reject 되어야 한다."""
    policy = _policy(
        global_bucket=TokenBucket(capacity=1.0, refill_per_sec=0.0, tokens=0.0, last_ts=0.0),
        lang_bucket=TokenBucket(capacity=1.0, refill_per_sec=0.0, tokens=0.0, last_ts=0.0),
        ws_bucket=TokenBucket(capacity=1.0, refill_per_sec=0.0, tokens=0.0, last_ts=0.0),
    )
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.RENAME_DEFINITION_PRECISION,
            mode=L5RequestMode.BATCH,
            workspace_uid="ws",
            total_rate=0.99,
            batch_rate=0.99,
            cooldown_active=False,
            cost=10,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is False
    assert decision.reject_stage == "mode"
    assert decision.reject_reason is L5RejectReason.MODE_NOT_ALLOWED


def test_l5_admission_reject_precedence_cooldown_before_rate() -> None:
    """문서 순서대로 cooldown은 rate/burst보다 먼저 reject 되어야 한다."""
    policy = _policy()
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.USER_INTERACTIVE,
            mode=L5RequestMode.INTERACTIVE,
            workspace_uid="ws",
            total_rate=0.99,
            batch_rate=0.99,
            cooldown_active=True,
            cost=1,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is False
    assert decision.reject_stage == "cooldown"
    assert decision.reject_reason is L5RejectReason.COOLDOWN_ACTIVE


def test_l5_admission_rejects_on_workspace_budget_exceeded() -> None:
    policy = _policy(
        global_bucket=_bucket(),
        lang_bucket=_bucket(),
        ws_bucket=TokenBucket(capacity=1.0, refill_per_sec=0.0, tokens=0.0, last_ts=0.0),
    )
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.USER_INTERACTIVE,
            mode=L5RequestMode.INTERACTIVE,
            workspace_uid="ws",
            total_rate=0.0,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is False
    assert decision.reject_reason is L5RejectReason.PRESSURE_WORKSPACE_EXCEEDED


def test_l5_admission_accepts_when_all_contracts_pass() -> None:
    policy = _policy(
        config=L5AdmissionPolicyConfig(
            language_policy_map={
                "python": LanguageL5Policy(
                    enabled=True,
                    mode_allow={
                        L5RequestMode.INTERACTIVE: (L5ReasonCode.USER_INTERACTIVE,),
                        L5RequestMode.BATCH: (L5ReasonCode.GOLDENSET_COVERAGE,),
                    },
                    cost_multiplier=1.0,
                    default_reason_weight=1.0,
                )
            }
        )
    )
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.USER_INTERACTIVE,
            mode=L5RequestMode.INTERACTIVE,
            workspace_uid="ws",
            total_rate=0.01,
            batch_rate=0.0,
            cooldown_active=False,
            cost=2,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is True
    assert decision.reject_reason is None
    assert decision.policy_version == 1


def test_l5_unknown_language_batch_index_build_is_blocked() -> None:
    policy = _policy()
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.REGRESSION_SAMPLING,
            mode=L5RequestMode.BATCH,
            workspace_uid="ws",
            total_rate=0.0,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=1.0,
            workload_kind="INDEX_BUILD",
        ),
        language_key="unknown-lang",
    )
    assert decision.admit_l5 is False
    assert decision.reject_reason is L5RejectReason.MODE_NOT_ALLOWED


def test_l5_burst_budget_reject_does_not_leak_global_tokens() -> None:
    global_bucket = TokenBucket(capacity=10.0, refill_per_sec=0.0, tokens=10.0, last_ts=0.0)
    lang_bucket = TokenBucket(capacity=1.0, refill_per_sec=0.0, tokens=0.0, last_ts=0.0)
    ws_bucket = TokenBucket(capacity=10.0, refill_per_sec=0.0, tokens=10.0, last_ts=0.0)
    policy = _policy(
        config=L5AdmissionPolicyConfig(
            language_policy_map={
                "python": LanguageL5Policy(
                    enabled=True,
                    mode_allow={
                        L5RequestMode.INTERACTIVE: (L5ReasonCode.USER_INTERACTIVE,),
                        L5RequestMode.BATCH: (L5ReasonCode.GOLDENSET_COVERAGE,),
                    },
                ),
            },
        ),
        global_bucket=global_bucket,
        lang_bucket=lang_bucket,
        ws_bucket=ws_bucket,
    )
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.USER_INTERACTIVE,
            mode=L5RequestMode.INTERACTIVE,
            workspace_uid="ws",
            total_rate=0.0,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is False
    assert decision.reject_reason is L5RejectReason.PRESSURE_BURST_EXCEEDED
    assert global_bucket.tokens == 10.0


def test_l5_starvation_guard_blocks_interactive_when_batch_call_reserve_low() -> None:
    policy = _policy(
        config=L5AdmissionPolicyConfig(
            l5_call_rate_total_max=0.05,
            l5_call_rate_batch_max=0.01,
            starvation_guard_enabled=True,
            min_batch_call_reserve_ratio=0.5,
            language_policy_map={
                "python": LanguageL5Policy(
                    enabled=True,
                    mode_allow={
                        L5RequestMode.INTERACTIVE: (L5ReasonCode.USER_INTERACTIVE,),
                        L5RequestMode.BATCH: (L5ReasonCode.GOLDENSET_COVERAGE,),
                    },
                )
            },
        )
    )
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.USER_INTERACTIVE,
            mode=L5RequestMode.INTERACTIVE,
            workspace_uid="ws",
            total_rate=0.049,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is False
    assert decision.reject_stage == "starvation"
    assert decision.primary_cause == "starvation_guard_call_reserve"


def test_l5_starvation_guard_blocks_interactive_when_token_reserve_would_be_broken() -> None:
    global_bucket = TokenBucket(capacity=10.0, refill_per_sec=0.0, tokens=1.0, last_ts=0.0)
    lang_bucket = TokenBucket(capacity=10.0, refill_per_sec=0.0, tokens=10.0, last_ts=0.0)
    ws_bucket = TokenBucket(capacity=10.0, refill_per_sec=0.0, tokens=10.0, last_ts=0.0)
    policy = _policy(
        config=L5AdmissionPolicyConfig(
            starvation_guard_enabled=True,
            min_batch_token_reserve_ratio=0.2,
            language_policy_map={
                "python": LanguageL5Policy(
                    enabled=True,
                    mode_allow={
                        L5RequestMode.INTERACTIVE: (L5ReasonCode.USER_INTERACTIVE,),
                        L5RequestMode.BATCH: (L5ReasonCode.GOLDENSET_COVERAGE,),
                    },
                )
            },
        ),
        global_bucket=global_bucket,
        lang_bucket=lang_bucket,
        ws_bucket=ws_bucket,
    )
    decision = policy.evaluate(
        admission=L5AdmissionInput(
            reason_code=L5ReasonCode.USER_INTERACTIVE,
            mode=L5RequestMode.INTERACTIVE,
            workspace_uid="ws",
            total_rate=0.0,
            batch_rate=0.0,
            cooldown_active=False,
            cost=1,
            now_ts=1.0,
        ),
        language_key="python",
    )
    assert decision.admit_l5 is False
    assert decision.reject_stage == "starvation"
    assert decision.primary_cause == "starvation_guard_token_reserve_global"
