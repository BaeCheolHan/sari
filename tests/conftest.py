from __future__ import annotations

import os
import sys

import pytest


def pytest_sessionstart(session: pytest.Session) -> None:  # noqa: ARG001
    if os.environ.get("SARI_SKIP_PYTEST_ENV_GUARD") == "1":
        return
    executable = sys.executable.replace("\\", "/")
    if "/.venv/" in executable:
        return
    raise pytest.UsageError(
        "This test suite must run with the project virtualenv.\n"
        f"Current interpreter: {sys.executable}\n"
        "Run tests with: uv run pytest -q .\n"
        "If you intentionally need to bypass this guard, set "
        "SARI_SKIP_PYTEST_ENV_GUARD=1."
    )
