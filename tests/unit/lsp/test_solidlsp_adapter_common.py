"""solidlsp 어댑터 공통 유틸 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from solidlsp.language_servers._adapter_common import (
    ensure_commands_available,
    ensure_paths_exist,
    first_executable_path,
)


def test_ensure_commands_available_raises_explicit_error_for_missing_command() -> None:
    """필수 명령이 없으면 명시적인 RuntimeError를 발생시켜야 한다."""
    with pytest.raises(RuntimeError, match="missing required commands"):
        ensure_commands_available(["__missing_command_for_test__"])


def test_ensure_paths_exist_raises_explicit_error_for_missing_path(tmp_path: Path) -> None:
    """필수 경로 누락은 assert가 아니라 명시적 RuntimeError여야 한다."""
    missing = tmp_path / "not-found"
    with pytest.raises(RuntimeError, match="missing required paths"):
        ensure_paths_exist([str(missing)], context="jdtls")


def test_first_executable_path_returns_first_existing_and_executable(tmp_path: Path) -> None:
    """후보 경로 중 실행 가능한 첫 경로를 반환해야 한다."""
    candidate = tmp_path / "tool"
    candidate.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    candidate.chmod(0o755)
    resolved = first_executable_path([str(candidate), str(tmp_path / "other")])
    assert resolved == str(candidate)
