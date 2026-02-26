from __future__ import annotations

from sari.services.collection.lsp_symbol_normalizer_service import LspSymbolNormalizerService


def test_normalize_symbols_maps_location_and_keys() -> None:
    service = LspSymbolNormalizerService(
        normalize_location=lambda **kwargs: "src/other.py",
        build_symbol_key=lambda **kwargs: f"k:{kwargs['relative_path']}:{kwargs['symbol'].get('name')}",
        resolve_symbol_depth=lambda symbol: 2,
        resolve_container_name=lambda symbol: "Parent",
    )
    raw_symbols = [
        {
            "name": "Child",
            "kind": "method",
            "location": {
                "range": {
                    "start": {"line": 10},
                    "end": {"line": 12},
                }
            },
            "parent": {"name": "Parent", "kind": "class"},
        }
    ]

    out = service.normalize_symbols(
        repo_root="/repo",
        normalized_relative_path="src/a.py",
        raw_symbols=raw_symbols,
    )

    assert len(out) == 1
    item = out[0]
    assert item["name"] == "Child"
    assert item["kind"] == "method"
    assert item["line"] == 10
    assert item["end_line"] == 12
    assert item["symbol_key"].startswith("k:src/other.py")
    assert item["parent_symbol_key"].startswith("k:src/other.py")
    assert item["depth"] == 2
    assert item["container_name"] == "Parent"


def test_normalize_symbols_skips_non_dict_items() -> None:
    service = LspSymbolNormalizerService(
        normalize_location=lambda **kwargs: kwargs["fallback_relative_path"],
        build_symbol_key=lambda **kwargs: "k",
        resolve_symbol_depth=lambda symbol: 0,
        resolve_container_name=lambda symbol: None,
    )

    out = service.normalize_symbols(
        repo_root="/repo",
        normalized_relative_path="src/a.py",
        raw_symbols=["x", 1, {"name": "A", "kind": "class"}],
    )

    assert len(out) == 1
    assert out[0]["name"] == "A"
