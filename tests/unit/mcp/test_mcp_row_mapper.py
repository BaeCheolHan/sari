from __future__ import annotations

import pytest

from sari.mcp.tools.row_mapper import row_to_item, rows_to_items


class _FakeRow:
    def __init__(self, value: str) -> None:
        self._value = value

    def to_dict(self) -> dict[str, object]:
        return {"name": self._value}


def test_row_to_item_accepts_mapping() -> None:
    item = row_to_item({"a": 1, "b": "x"})
    assert item == {"a": 1, "b": "x"}


def test_rows_to_items_accepts_to_dict_rows() -> None:
    items = rows_to_items([_FakeRow("foo"), _FakeRow("bar")])
    assert items == [{"name": "foo"}, {"name": "bar"}]


def test_row_to_item_raises_type_error_for_unsupported_row() -> None:
    with pytest.raises(TypeError):
        row_to_item(123)

