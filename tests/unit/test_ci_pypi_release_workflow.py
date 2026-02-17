"""PyPI 배포 워크플로 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path


def test_release_script_exists_and_enforces_build_check_contract() -> None:
    """배포 스크립트는 빌드/무결성 검사 계약을 강제해야 한다."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "tools" / "ci" / "release_pypi.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "python3 -m build" in content
    assert "python3 -m twine check" in content
    assert "set -euo pipefail" in content


def test_release_workflow_supports_tag_and_manual_dispatch() -> None:
    """배포 워크플로는 태그 배포와 수동 실행을 모두 지원해야 한다."""
    root = Path(__file__).resolve().parents[2]
    workflow_path = root / ".github" / "workflows" / "release-pypi.yml"
    content = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in content
    assert "tags:" in content
    assert "v*" in content
    assert "id-token: write" in content
    assert "pypa/gh-action-pypi-publish" in content
