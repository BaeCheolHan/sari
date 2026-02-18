"""solidlsp 서브프로세스 명령 정규화 보안 경계를 검증한다."""

from __future__ import annotations

import pytest

from solidlsp.language_servers.common import _normalize_command_args as normalize_runtime_command_args
from solidlsp.ls_handler import _normalize_command_args as normalize_launch_command_args


def test_normalize_launch_command_args_splits_string_command() -> None:
    """문자열 커맨드는 argv 리스트로 분해되어야 한다."""
    assert normalize_launch_command_args("gopls serve", is_windows=False) == ["gopls", "serve"]


def test_normalize_runtime_command_args_preserves_list_command() -> None:
    """리스트 커맨드는 동일 순서로 유지되어야 한다."""
    assert normalize_runtime_command_args(["npm", "install", "--prefix", "./"], is_windows=False) == [
        "npm",
        "install",
        "--prefix",
        "./",
    ]


def test_normalize_command_args_rejects_empty_command() -> None:
    """빈 커맨드는 명시 예외로 차단되어야 한다."""
    with pytest.raises(RuntimeError):
        normalize_launch_command_args("", is_windows=False)
