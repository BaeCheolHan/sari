"""L5 admission 정책(사유/모드/예산/버스트) 판정을 제공한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha1

from sari.core.models import L4AdmissionDecisionDTO, L5ReasonCode, L5RejectReason, L5RequestMode


def classify_l5_request_mode(*, is_user_triggered: bool, is_sync_request: bool) -> L5RequestMode:
    """L5 요청 모드를 분류한다.

    규칙:
    - 사용자 트리거 + 동기 요청이면 interactive
    - 나머지는 batch (애매하면 보수적으로 batch)
    """
    if bool(is_user_triggered) and bool(is_sync_request):
        return L5RequestMode.INTERACTIVE
    return L5RequestMode.BATCH


def normalize_workspace_uid(workspace_root: str) -> str:
    """workspace root 기반의 안정적인 UID를 생성한다."""
    normalized = workspace_root.strip()
    return sha1(normalized.encode("utf-8")).hexdigest()


@dataclass
class TokenBucket:
    """간단한 토큰 버킷 구현."""

    capacity: float
    refill_per_sec: float
    tokens: float
    last_ts: float

    def refill(self, now_ts: float) -> None:
        elapsed = max(0.0, float(now_ts) - float(self.last_ts))
        self.last_ts = float(now_ts)
        if elapsed <= 0.0:
            return
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)


@dataclass(frozen=True)
class L5AdmissionInput:
    """L5 admission 입력값."""

    reason_code: L5ReasonCode | None
    mode: L5RequestMode
    workspace_uid: str
    total_rate: float
    batch_rate: float
    cooldown_active: bool
    cost: int
    now_ts: float
    caller: str = "unknown"
    workload_kind: str = "default"


@dataclass(frozen=True)
class LanguageL5Policy:
    """언어별 L5 admission 정책."""

    enabled: bool = True
    mode_allow: dict[L5RequestMode, tuple[L5ReasonCode, ...]] = field(default_factory=dict)
    cost_multiplier: float = 1.0
    default_reason_weight: float = 1.0
    reason_weight_map: dict[L5ReasonCode, float] = field(default_factory=dict)


@dataclass(frozen=True)
class L5AdmissionPolicyConfig:
    """L5 admission 정책 설정."""

    l5_call_rate_total_max: float = 0.05
    l5_call_rate_batch_max: float = 0.01
    policy_version: int = 1
    unknown_reason_fallback_per_10sec_per_workspace_caller_max: int = 1
    unknown_min_pass_per_window: int = 1
    starvation_guard_enabled: bool = True
    min_batch_call_reserve_ratio: float = 0.1
    min_batch_token_reserve_ratio: float = 0.1
    default_language_policy: LanguageL5Policy = field(
        default_factory=lambda: LanguageL5Policy(
            enabled=True,
            mode_allow={
                L5RequestMode.INTERACTIVE: (
                    L5ReasonCode.USER_INTERACTIVE,
                    L5ReasonCode.UNRESOLVED_SYMBOL,
                    L5ReasonCode.USER_INTERACTIVE_UNKNOWN,
                ),
                L5RequestMode.BATCH: (
                    L5ReasonCode.REGRESSION_SAMPLING,
                ),
            },
            cost_multiplier=8.0,
            default_reason_weight=1.0,
            reason_weight_map={
                L5ReasonCode.REGRESSION_SAMPLING: 3.0,
                L5ReasonCode.USER_INTERACTIVE_UNKNOWN: 5.0,
            },
        )
    )
    language_policy_map: dict[str, LanguageL5Policy] = field(default_factory=dict)


class L5AdmissionPolicy:
    """L5 admission 판정을 수행한다."""

    _WEIGHT_MIN = 0.1
    _WEIGHT_MAX = 10.0

    def __init__(
        self,
        *,
        config: L5AdmissionPolicyConfig,
        global_bucket: TokenBucket,
        lang_bucket_provider: Callable[[str], TokenBucket],
        workspace_bucket_provider: Callable[[str], TokenBucket],
    ) -> None:
        self._config = config
        self._global_bucket = global_bucket
        self._lang_bucket_provider = lang_bucket_provider
        self._workspace_bucket_provider = workspace_bucket_provider
        self._unknown_reason_budget_window_sec = 10.0
        self._unknown_reason_counts: dict[str, tuple[float, int]] = {}
        self._unknown_lang_min_pass_window_sec = 10.0
        self._unknown_lang_min_pass_counts: dict[str, tuple[float, int]] = {}

    def evaluate(self, *, admission: L5AdmissionInput, language_key: str) -> L4AdmissionDecisionDTO:
        normalized_language = str(language_key or "").strip().lower()
        policy = self._resolve_language_policy(normalized_language)

        normalized_reason = self._normalize_reason(
            requested_reason=admission.reason_code,
            mode=admission.mode,
            workspace_uid=admission.workspace_uid,
            caller=admission.caller,
            now_ts=float(admission.now_ts),
        )
        if normalized_reason is None:
            return self._reject(
                admission=admission,
                reason_code=None,
                reject_reason=L5RejectReason.REASON_MISSING,
                stage="reason",
                primary_cause="reason_missing",
            )

        if not policy.enabled:
            return self._reject(
                admission=admission,
                reason_code=normalized_reason,
                reject_reason=L5RejectReason.POLICY_DISABLED,
                stage="enabled",
                primary_cause="policy_disabled",
            )

        allowed_reasons = policy.mode_allow.get(admission.mode, ())
        if normalized_reason not in allowed_reasons:
            return self._reject(
                admission=admission,
                reason_code=normalized_reason,
                reject_reason=L5RejectReason.MODE_NOT_ALLOWED,
                stage="mode",
                primary_cause="mode_not_allowed",
            )

        is_unknown_language = normalized_language not in self._config.language_policy_map
        if (
            is_unknown_language
            and admission.mode is L5RequestMode.BATCH
            and admission.workload_kind.strip().upper() == "INDEX_BUILD"
        ):
            return self._reject(
                admission=admission,
                reason_code=normalized_reason,
                reject_reason=L5RejectReason.MODE_NOT_ALLOWED,
                stage="mode",
                primary_cause="unknown_batch_index_build_blocked",
            )

        if admission.cooldown_active:
            return self._reject(
                admission=admission,
                reason_code=normalized_reason,
                reject_reason=L5RejectReason.COOLDOWN_ACTIVE,
                stage="cooldown",
                primary_cause="cooldown_active",
            )

        starvation_guard_reject = self._check_starvation_guard(
            admission=admission,
            language_key=normalized_language,
        )
        if starvation_guard_reject is not None:
            reject_reason, stage, primary_cause = starvation_guard_reject
            return self._reject(
                admission=admission,
                reason_code=normalized_reason,
                reject_reason=reject_reason,
                stage=stage,
                primary_cause=primary_cause,
            )

        total_rate_max = float(self._config.l5_call_rate_total_max)
        batch_rate_max = float(self._config.l5_call_rate_batch_max)
        if admission.total_rate > total_rate_max:
            return self._reject(
                admission=admission,
                reason_code=normalized_reason,
                reject_reason=L5RejectReason.PRESSURE_RATE_EXCEEDED,
                stage="rate",
                primary_cause="pressure_rate_exceeded",
            )
        if admission.mode is L5RequestMode.BATCH and admission.batch_rate > batch_rate_max:
            return self._reject(
                admission=admission,
                reason_code=normalized_reason,
                reject_reason=L5RejectReason.PRESSURE_RATE_EXCEEDED,
                stage="rate",
                primary_cause="pressure_rate_exceeded",
            )

        effective_cost = self._effective_cost(
            admission=admission,
            policy=policy,
            reason_code=normalized_reason,
        )

        burst_result = self._consume_burst_budget(
            admission=admission,
            language_key=normalized_language,
            effective_cost=float(effective_cost),
            is_unknown_language=is_unknown_language,
        )
        if burst_result is not None:
            reject_reason, stage, primary_cause = burst_result
            return self._reject(
                admission=admission,
                reason_code=normalized_reason,
                reject_reason=reject_reason,
                stage=stage,
                primary_cause=primary_cause,
                budget_cost=effective_cost,
            )

        return L4AdmissionDecisionDTO(
            admit_l5=True,
            reason_code=normalized_reason,
            mode=admission.mode,
            workspace_uid=admission.workspace_uid,
            budget_cost=effective_cost,
            policy_version=self._config.policy_version,
        )

    def _check_starvation_guard(
        self,
        *,
        admission: L5AdmissionInput,
        language_key: str,
    ) -> tuple[L5RejectReason, str, str] | None:
        if not bool(self._config.starvation_guard_enabled):
            return None
        if admission.mode is not L5RequestMode.INTERACTIVE:
            return None
        total_rate_max = float(self._config.l5_call_rate_total_max)
        batch_rate_max = float(self._config.l5_call_rate_batch_max)
        if total_rate_max <= 0.0 or batch_rate_max <= 0.0:
            return None
        reserve_call_ratio = max(0.0, min(1.0, float(self._config.min_batch_call_reserve_ratio)))
        # interactive가 총량 상단 근처에 도달했고 batch 점유가 reserve 이하이면 사전 차단한다.
        if admission.total_rate >= (total_rate_max * 0.95) and admission.batch_rate <= (batch_rate_max * reserve_call_ratio):
            return (L5RejectReason.PRESSURE_RATE_EXCEEDED, "starvation", "starvation_guard_call_reserve")

        now_ts = float(admission.now_ts)
        cost = max(1.0, float(admission.cost))
        reserve_token_ratio = max(0.0, min(1.0, float(self._config.min_batch_token_reserve_ratio)))
        global_bucket = self._global_bucket
        lang_bucket = self._lang_bucket_provider(language_key)
        workspace_bucket = self._workspace_bucket_provider(admission.workspace_uid)
        global_bucket.refill(now_ts)
        lang_bucket.refill(now_ts)
        workspace_bucket.refill(now_ts)
        reserved_global = float(global_bucket.capacity) * reserve_token_ratio
        reserved_lang = float(lang_bucket.capacity) * reserve_token_ratio
        reserved_workspace = float(workspace_bucket.capacity) * reserve_token_ratio
        if (global_bucket.tokens - cost) < reserved_global:
            return (L5RejectReason.PRESSURE_BURST_EXCEEDED, "starvation", "starvation_guard_token_reserve_global")
        if (lang_bucket.tokens - cost) < reserved_lang:
            return (L5RejectReason.PRESSURE_BURST_EXCEEDED, "starvation", "starvation_guard_token_reserve_lang")
        if (workspace_bucket.tokens - cost) < reserved_workspace:
            return (L5RejectReason.PRESSURE_WORKSPACE_EXCEEDED, "starvation", "starvation_guard_token_reserve_workspace")
        return None

    def _resolve_language_policy(self, language_key: str) -> LanguageL5Policy:
        if language_key in self._config.language_policy_map:
            return self._config.language_policy_map[language_key]
        return self._config.default_language_policy

    def _normalize_reason(
        self,
        *,
        requested_reason: L5ReasonCode | None,
        mode: L5RequestMode,
        workspace_uid: str,
        caller: str,
        now_ts: float,
    ) -> L5ReasonCode | None:
        if requested_reason is not None:
            return requested_reason
        if mode is not L5RequestMode.INTERACTIVE:
            return None
        key = f"{workspace_uid}:{caller}"
        window_start, count = self._unknown_reason_counts.get(key, (now_ts, 0))
        if now_ts - window_start >= self._unknown_reason_budget_window_sec:
            window_start, count = now_ts, 0
        if count >= int(self._config.unknown_reason_fallback_per_10sec_per_workspace_caller_max):
            self._unknown_reason_counts[key] = (window_start, count)
            return None
        self._unknown_reason_counts[key] = (window_start, count + 1)
        return L5ReasonCode.USER_INTERACTIVE_UNKNOWN

    def _effective_cost(self, *, admission: L5AdmissionInput, policy: LanguageL5Policy, reason_code: L5ReasonCode) -> int:
        base_cost = max(1.0, float(admission.cost))
        reason_weight = float(policy.reason_weight_map.get(reason_code, policy.default_reason_weight))
        reason_weight = max(self._WEIGHT_MIN, min(self._WEIGHT_MAX, reason_weight))
        cost_multiplier = max(self._WEIGHT_MIN, min(self._WEIGHT_MAX, float(policy.cost_multiplier)))
        return max(1, int(round(base_cost * cost_multiplier * reason_weight)))

    def _consume_burst_budget(
        self,
        *,
        admission: L5AdmissionInput,
        language_key: str,
        effective_cost: float,
        is_unknown_language: bool,
    ) -> tuple[L5RejectReason, str, str] | None:
        cost = max(1.0, float(effective_cost))
        now_ts = float(admission.now_ts)
        lang_bucket = self._lang_bucket_provider(language_key)
        workspace_bucket = self._workspace_bucket_provider(admission.workspace_uid)
        self._global_bucket.refill(now_ts)
        lang_bucket.refill(now_ts)
        workspace_bucket.refill(now_ts)

        if self._global_bucket.tokens < cost:
            return (L5RejectReason.PRESSURE_BURST_EXCEEDED, "burst", "pressure_burst_exceeded")
        if workspace_bucket.tokens < cost:
            return (L5RejectReason.PRESSURE_WORKSPACE_EXCEEDED, "workspace", "pressure_workspace_exceeded")

        allow_unknown_min_pass = False
        if is_unknown_language:
            window_start, count = self._unknown_lang_min_pass_counts.get(language_key, (now_ts, 0))
            if now_ts - window_start >= self._unknown_lang_min_pass_window_sec:
                window_start, count = now_ts, 0
            min_pass = max(0, int(self._config.unknown_min_pass_per_window))
            if count < min_pass:
                allow_unknown_min_pass = True
                self._unknown_lang_min_pass_counts[language_key] = (window_start, count + 1)
            else:
                self._unknown_lang_min_pass_counts[language_key] = (window_start, count)

        if not allow_unknown_min_pass and lang_bucket.tokens < cost:
            return (L5RejectReason.PRESSURE_BURST_EXCEEDED, "burst", "pressure_burst_exceeded")

        self._global_bucket.tokens -= cost
        workspace_bucket.tokens -= cost
        if not allow_unknown_min_pass:
            lang_bucket.tokens -= cost
        return None

    def _reject(
        self,
        *,
        admission: L5AdmissionInput,
        reason_code: L5ReasonCode | None,
        reject_reason: L5RejectReason,
        stage: str,
        primary_cause: str,
        budget_cost: int | None = None,
    ) -> L4AdmissionDecisionDTO:
        return L4AdmissionDecisionDTO(
            admit_l5=False,
            reason_code=reason_code,
            reject_reason=reject_reason,
            mode=admission.mode,
            workspace_uid=admission.workspace_uid,
            budget_cost=int(admission.cost if budget_cost is None else budget_cost),
            primary_cause=primary_cause,
            reject_stage=stage,
            policy_version=self._config.policy_version,
        )
