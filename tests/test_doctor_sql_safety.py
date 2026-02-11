import pytest

from sari.mcp.tools import doctor


def test_doctor_pragma_table_name_allows_known_tables():
    assert doctor._safe_pragma_table_name("symbols") == "symbols"
    assert doctor._safe_pragma_table_name("symbol_relations") == "symbol_relations"


def test_doctor_pragma_table_name_rejects_unknown_or_unsafe_names():
    with pytest.raises(ValueError):
        doctor._safe_pragma_table_name("symbols; DROP TABLE files;--")
    with pytest.raises(ValueError):
        doctor._safe_pragma_table_name("sqlite_master")
