"""solidlsp에서 참조하는 최소 텍스트 타입을 제공한다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(kw_only=True)
class MatchedConsecutiveLines:
    """연속 라인 매칭 결과를 표현한다."""

    lines: list[str]
    source_file_path: str | None = None
