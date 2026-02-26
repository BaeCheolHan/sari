from __future__ import annotations

from sari.core.models import L5ReasonCode, L5RequestMode
from sari.services.collection.l5.l5_default_policy_builder import build_default_language_policy_map


def test_build_default_language_policy_map_contains_expected_modes() -> None:
    policy_map = build_default_language_policy_map(["python", "java"])
    assert "python" in policy_map
    assert "java" in policy_map
    py_policy = policy_map["python"]
    assert L5ReasonCode.USER_INTERACTIVE in py_policy.mode_allow[L5RequestMode.INTERACTIVE]
    assert L5ReasonCode.GOLDENSET_COVERAGE in py_policy.mode_allow[L5RequestMode.BATCH]
