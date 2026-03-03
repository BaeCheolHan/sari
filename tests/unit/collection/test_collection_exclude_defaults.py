"""기본 수집 제외 패턴 정책을 검증한다."""

from sari.core.config import DEFAULT_COLLECTION_EXCLUDE_GLOBS


def test_default_exclude_globs_include_build_artifact_dirs() -> None:
    """빌드 산출물 디렉터리가 기본 제외 패턴에 포함되어야 한다."""
    required = {
        "**/bin/**",
        "**/build/**",
        "**/target/**",
        "**/generated-sources/**",
        "**/.gradle/**",
        "**/node_modules/**",
    }
    for pattern in required:
        assert pattern in DEFAULT_COLLECTION_EXCLUDE_GLOBS
