"""L5 admission 기본 언어 정책 구성 유틸."""

from __future__ import annotations

from collections.abc import Iterable

from sari.core.models import L5ReasonCode, L5RequestMode
from sari.services.collection.l5.l5_admission_policy import LanguageL5Policy


def build_default_language_policy_map(enabled_language_names: Iterable[str]) -> dict[str, LanguageL5Policy]:
    """전 언어 오픈 + 예산 기반 제어 기본 정책을 생성한다."""
    policy = LanguageL5Policy(
        enabled=True,
        mode_allow={
            L5RequestMode.INTERACTIVE: (
                L5ReasonCode.USER_INTERACTIVE,
                L5ReasonCode.UNRESOLVED_SYMBOL,
                L5ReasonCode.CROSS_FILE_REFERENCE_REQUIRED,
                L5ReasonCode.RENAME_DEFINITION_PRECISION,
                L5ReasonCode.USER_INTERACTIVE_UNKNOWN,
            ),
            L5RequestMode.BATCH: (
                L5ReasonCode.GOLDENSET_COVERAGE,
                L5ReasonCode.REGRESSION_SAMPLING,
                L5ReasonCode.UNRESOLVED_SYMBOL,
            ),
        },
        cost_multiplier=1.0,
        default_reason_weight=1.0,
        reason_weight_map={
            L5ReasonCode.RENAME_DEFINITION_PRECISION: 2.0,
            L5ReasonCode.GOLDENSET_COVERAGE: 1.5,
        },
    )
    out: dict[str, LanguageL5Policy] = {}
    for language in enabled_language_names:
        out[str(language).strip().lower()] = policy
    return out
