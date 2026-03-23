from __future__ import annotations

from pathlib import Path
import tomllib

from sari import __version__


def test_runtime_version_matches_pyproject_version() -> None:
    root = Path(__file__).resolve().parents[3]
    with (root / "pyproject.toml").open("rb") as fp:
        pyproject = tomllib.load(fp)

    assert __version__ == pyproject["project"]["version"]
