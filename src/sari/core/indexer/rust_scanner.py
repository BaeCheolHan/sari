from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple


_RUST_BIN_CACHE: Optional[Path] = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _rust_scanner_bin() -> Path:
    global _RUST_BIN_CACHE
    if _RUST_BIN_CACHE is not None:
        return _RUST_BIN_CACHE

    root = _repo_root()
    exe = "sari_rust_scanner.exe" if os.name == "nt" else "sari_rust_scanner"
    bin_path = root / "tools" / "rust_scanner" / "target" / "release" / exe
    if not bin_path.exists():
        subprocess.run(
            ["cargo", "build", "--release"],
            cwd=str(root / "tools" / "rust_scanner"),
            check=True,
            capture_output=True,
            text=True,
        )
    _RUST_BIN_CACHE = bin_path
    return bin_path


def iter_rust_scan_entries(
    root: Path,
    *,
    max_depth: int,
    follow_symlinks: bool,
    exclude_dirs: list[str],
) -> Iterator[Tuple[Path, int, int]]:
    bin_path = _rust_scanner_bin()
    cmd = [
        str(bin_path),
        "--root",
        str(root),
        "--max-depth",
        str(max(0, int(max_depth))),
    ]
    if follow_symlinks:
        cmd.append("--follow-symlinks")
    for item in exclude_dirs:
        val = str(item or "").strip()
        if val:
            cmd.extend(["--exclude-dir", val])

    proc = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        timeout=float(os.environ.get("SARI_RUST_SCANNER_TIMEOUT_SEC", "120") or 120),
    )

    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        p, mtime_s, size_s = parts
        try:
            yield Path(p), int(mtime_s), int(size_s)
        except Exception:
            continue
