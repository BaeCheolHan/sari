from __future__ import annotations

import os
import sys

import pytest


def _is_virtual_environment() -> bool:
    if os.environ.get("VIRTUAL_ENV"):
        return True
    if os.environ.get("CONDA_PREFIX"):
        return True
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def pytest_sessionstart(session: pytest.Session) -> None:  # noqa: ARG001
    if _is_virtual_environment():
        return
    raise pytest.UsageError(
        "This test suite must run in a virtual environment with project dependencies.\n"
        f"Current interpreter: {sys.executable}\n"
        "Run tests with: uv run pytest -q ."
    )
