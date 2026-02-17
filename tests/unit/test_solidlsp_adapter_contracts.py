"""Top5 solidlsp 어댑터의 공통 유틸 사용 계약을 검증한다."""

from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_top5_adapters_use_adapter_common_contract() -> None:
    """Top5 어댑터가 공통 모듈을 통해 명시적 경계 검사를 수행해야 한다."""
    root = Path(__file__).resolve().parents[2] / "src" / "solidlsp" / "language_servers"

    vue = _read(root / "vue_language_server.py")
    csharp = _read(root / "csharp_language_server.py")
    pascal = _read(root / "pascal_server.py")
    jdtls = _read(root / "eclipse_jdtls.py")
    rust = _read(root / "rust_analyzer.py")

    assert "ensure_commands_available" in vue
    assert "ensure_paths_exist" in csharp
    assert "ensure_paths_exist" in pascal
    assert "ensure_paths_exist" in jdtls
    assert "first_executable_path" in rust
