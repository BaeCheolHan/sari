"""run_mode profile 정책 및 env read 제약."""

from __future__ import annotations

import os

_RUN_MODE_RELEASE_ALIASES = frozenset({"release"})
_RUN_MODE_TEST_ALIASES = frozenset({"test"})


def normalize_run_mode(raw: str) -> str:
    """run_mode 입력값을 정규화한다."""
    normalized = str(raw).strip().lower()
    if normalized in _RUN_MODE_RELEASE_ALIASES:
        return "release"
    if normalized in _RUN_MODE_TEST_ALIASES:
        return "test"
    if normalized == "prod":
        return "prod"
    return "dev"


def build_release_env_allowlist() -> set[str]:
    """release 모드에서만 허용할 환경변수 목록.

    각 항목은 운영 배포 시 즉시 대응이 필요한 값만 남긴다.
    """
    return {
        # DB 파일 위치
        "SARI_DB_PATH",
        # 프로파일 선택(release/test/prod/dev)
        "SARI_RUN_MODE",
        # 수집 대상 확장자
        "SARI_COLLECTION_INCLUDE_EXT",
        # 수집 제외 글롭
        "SARI_COLLECTION_EXCLUDE_GLOBS",
        # 백엔드 선택(scan/tantivy)
        "SARI_CANDIDATE_BACKEND",
        # candidate fallback 허용 여부
        "SARI_CANDIDATE_FALLBACK_SCAN",
        # MCP daemon 라우팅 여부
        "SARI_MCP_FORWARD_TO_DAEMON",
        # MCP daemon 자동 시작
        "SARI_MCP_DAEMON_AUTOSTART",
        # MCP daemon 호출 타임아웃
        "SARI_MCP_DAEMON_TIMEOUT_SEC",
        # MCP search call 타임아웃
        "SARI_MCP_SEARCH_CALL_TIMEOUT_SEC",
        # MCP read call 타임아웃
        "SARI_MCP_READ_CALL_TIMEOUT_SEC",
    }


def read_env_or_default(*, env_key: str, default: str, allow_env_keys: set[str] | None) -> str:
    """release allowlist를 고려한 단일 env read helper."""
    if allow_env_keys is not None and env_key not in allow_env_keys:
        return default
    return os.getenv(env_key, default)
