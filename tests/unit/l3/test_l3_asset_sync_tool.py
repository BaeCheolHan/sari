from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def _write_assets(root: Path) -> None:
    (root / "queries" / "java").mkdir(parents=True, exist_ok=True)
    (root / "queries" / "javascript").mkdir(parents=True, exist_ok=True)
    (root / "queries" / "kotlin").mkdir(parents=True, exist_ok=True)
    (root / "queries" / "typescript").mkdir(parents=True, exist_ok=True)
    (root / "queries" / "python").mkdir(parents=True, exist_ok=True)
    (root / "mappings").mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text('{"version":"test"}', encoding="utf-8")
    (root / "mappings" / "default.yaml").write_text("{}", encoding="utf-8")
    (root / "queries" / "java" / "outline.scm").write_text("(class_declaration) @symbol.class", encoding="utf-8")
    (root / "queries" / "javascript" / "outline.scm").write_text("(class_declaration) @symbol.class", encoding="utf-8")
    (root / "queries" / "kotlin" / "outline.scm").write_text("(class_declaration) @symbol.class", encoding="utf-8")
    (root / "queries" / "typescript" / "outline.scm").write_text("(class_declaration) @symbol.class", encoding="utf-8")
    (root / "queries" / "python" / "outline.scm").write_text("(class_definition) @symbol.class", encoding="utf-8")


def test_sync_queries_run_writes_lock_file(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    _write_assets(assets)
    lock_path = tmp_path / "lock.json"
    script = Path(__file__).resolve().parents[3] / "tools" / "l3_assets" / "sync_queries.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--assets-root",
            str(assets),
            "--lock-path",
            str(lock_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert lock_path.is_file()
    assert '"status": "validated"' in lock_path.read_text(encoding="utf-8")


def test_sync_queries_merges_official_and_nvim_sources(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    _write_assets(assets)
    official = tmp_path / "official"
    nvim = tmp_path / "nvim"
    for lang in ("java", "javascript", "kotlin", "typescript", "python"):
        (official / lang).mkdir(parents=True, exist_ok=True)
        (nvim / lang).mkdir(parents=True, exist_ok=True)
        (official / lang / "tags.scm").write_text(
            f"; official {lang}\n(class_declaration) @symbol.class\n",
            encoding="utf-8",
        )
        (nvim / lang / "tags.scm").write_text(
            f"; nvim {lang}\n(method_declaration) @symbol.method\n",
            encoding="utf-8",
        )

    lock_path = tmp_path / "lock-sync.json"
    script = Path(__file__).resolve().parents[3] / "tools" / "l3_assets" / "sync_queries.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--assets-root",
            str(assets),
            "--lock-path",
            str(lock_path),
            "--sync",
            "--official-root",
            str(official),
            "--nvim-root",
            str(nvim),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    merged = (assets / "queries" / "java" / "outline.scm").read_text(encoding="utf-8")
    assert "; official java" in merged
    assert "; nvim java" in merged
    assert "; supplement:sari" in merged
    js_merged = (assets / "queries" / "javascript" / "outline.scm").read_text(encoding="utf-8")
    assert "function_declaration" in js_merged
    assert "function_expression" not in js_merged
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["status"] == "synced"
    assert lock["sync"]["languages"] == ["java", "javascript", "kotlin", "python", "typescript"]
