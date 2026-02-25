"""MCP tool 계층에서 DB row를 payload dict로 변환하는 공통 매퍼."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol, cast, runtime_checkable


@runtime_checkable
class DictConvertible(Protocol):
    """dict 직렬화가 가능한 row 계약."""

    def to_dict(self) -> dict[str, object]:
        """row를 payload dict로 변환한다."""


def row_to_item(row: object) -> dict[str, object]:
    """row 단건을 payload dict로 변환한다.

    정책:
    - 이미 Mapping이면 dict로 복사
    - to_dict()를 제공하면 사용
    - 그 외 타입은 TypeError로 실패시켜 조용한 침묵을 막는다.
    """
    if isinstance(row, Mapping):
        return dict(cast(Mapping[str, object], row))
    if isinstance(row, DictConvertible):
        return row.to_dict()
    raise TypeError(f"row does not support mapping serialization: {type(row).__name__}")


def rows_to_items(rows: Iterable[object]) -> list[dict[str, object]]:
    """row 목록을 payload dict 목록으로 변환한다."""
    return [row_to_item(row) for row in rows]

