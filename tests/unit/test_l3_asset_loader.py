from __future__ import annotations

from pathlib import Path

from sari.services.collection.l3.l3_asset_loader import L3AssetLoader


def test_asset_loader_loads_manifest_and_language_bundle() -> None:
    loader = L3AssetLoader()

    bundle = loader.load("java")

    assert loader.manifest_version != "unknown"
    assert bundle.language == "java"
    assert bundle.query_source is not None
    assert bundle.capture_to_kind.get("symbol.class") == "class"


def test_asset_loader_falls_back_to_default_mapping_for_unknown_language(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    (assets / "mappings").mkdir(parents=True, exist_ok=True)
    (assets / "queries").mkdir(parents=True, exist_ok=True)
    (assets / "manifest.json").write_text('{"version":"test"}', encoding="utf-8")
    (assets / "mappings" / "default.yaml").write_text(
        '{"kind_bucket_map":{"foo":"bar"},"capture_to_kind":{"symbol.function":"function"}}',
        encoding="utf-8",
    )

    loader = L3AssetLoader(assets_root=assets)
    bundle = loader.load("unknown_lang")

    assert bundle.language == "unknown_lang"
    assert bundle.kind_bucket_map.get("foo") == "bar"
    assert bundle.capture_to_kind.get("symbol.function") == "function"


def test_asset_loader_normalizes_js_to_javascript() -> None:
    loader = L3AssetLoader()

    bundle = loader.load("js")

    assert bundle.language == "javascript"


def test_asset_loader_loads_scala_bundle() -> None:
    loader = L3AssetLoader()

    bundle = loader.load("scala")

    assert bundle.language == "scala"
    assert bundle.query_source is not None
    assert bundle.capture_to_kind.get("symbol.method") == "method"


def test_asset_loader_exposes_last_load_error_for_invalid_mapping(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    (assets / "mappings").mkdir(parents=True, exist_ok=True)
    (assets / "queries").mkdir(parents=True, exist_ok=True)
    (assets / "manifest.json").write_text('{"version":"test"}', encoding="utf-8")
    (assets / "mappings" / "default.yaml").write_text("{invalid-json", encoding="utf-8")

    loader = L3AssetLoader(assets_root=assets)
    bundle = loader.load("unknown_lang")

    assert bundle.language == "unknown_lang"
    assert loader.last_load_error is not None
    assert str(loader.last_load_error).startswith("mapping_load_error:")
