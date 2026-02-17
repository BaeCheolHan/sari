"""solidlsp에서 사용하는 파일 경로 매칭 유틸을 제공한다."""

from __future__ import annotations

from pathlib import Path

from pathspec import PathSpec


def match_path(relative_path: str, spec: PathSpec, root_path: str) -> bool:
    """gitignore 규칙 기반 경로 매칭 결과를 반환한다."""
    root = Path(root_path).resolve()
    target = (root / relative_path).resolve()
    try:
        norm = target.relative_to(root).as_posix()
    except ValueError:
        return False
    return spec.match_file(norm)
