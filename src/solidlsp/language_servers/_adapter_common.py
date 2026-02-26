"""언어 서버 어댑터 공통 보조 유틸리티."""

from __future__ import annotations

import os
import shutil

from solidlsp.ls import get_current_process_env_snapshot


def ensure_commands_available(commands: list[str]) -> None:
    """필수 명령 존재 여부를 검사하고 누락 시 명시 오류를 발생시킨다."""
    missing: list[str] = []
    env_snapshot = get_current_process_env_snapshot()
    path_value = env_snapshot.get("PATH")
    for command in commands:
        if shutil.which(command, path=path_value) is None:
            missing.append(command)
    if len(missing) > 0:
        raise RuntimeError(f"missing required commands: {', '.join(missing)}")


def ensure_paths_exist(paths: list[str], context: str) -> None:
    """필수 파일/디렉터리 존재 여부를 검사하고 누락 시 명시 오류를 발생시킨다."""
    missing = [path for path in paths if not os.path.exists(path)]
    if len(missing) > 0:
        joined = ", ".join(missing)
        raise RuntimeError(f"missing required paths ({context}): {joined}")


def first_executable_path(candidates: list[str]) -> str | None:
    """실행 가능한 후보 경로 중 첫 번째 항목을 반환한다."""
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None
