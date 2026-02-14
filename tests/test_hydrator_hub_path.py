from sari.core.lsp.hydrator import hydrate_file_symbols_from_text


class _FakeHub:
    def request_document_symbols(self, *, source_path: str, source: str):
        return (
            True,
            [
                {
                    "symbol_id": "lsp:1",
                    "name": "hello",
                    "kind": "function",
                    "line": 1,
                    "end_line": 2,
                    "content": "",
                    "parent": "",
                    "meta_json": "{}",
                    "doc_comment": "",
                    "qualname": "hello",
                    "importance_score": 0.0,
                }
            ],
            "",
        )


class _FakeDB:
    def __init__(self) -> None:
        self.rows = []

    def upsert_symbols_tx(self, _cur, rows, *, root_id: str):
        self.rows.extend(rows)


def test_hydrator_uses_lsp_hub_first(monkeypatch):
    monkeypatch.setattr("sari.core.lsp.hydrator.get_lsp_hub", lambda: _FakeHub())
    db = _FakeDB()
    count, rows = hydrate_file_symbols_from_text(
        db=db,
        db_path="rid/main.py",
        source_path="/tmp/main.py",
        source="def hello():\n    return 1\n",
    )
    assert count == 1
    assert len(rows) == 1
    assert rows[0]["name"] == "hello"
    assert db.rows and db.rows[0]["name"] == "hello"

