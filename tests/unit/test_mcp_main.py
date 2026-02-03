import runpy
import types
import sys

import pytest


def test_main_routes_to_cli(monkeypatch):
    sys.modules.pop("mcp.__main__", None)
    monkeypatch.setattr(sys, "argv", ["-m", "mcp", "daemon"])
    import types
    dummy_cli = types.SimpleNamespace(main=lambda: 0)
    sys.modules["mcp.cli"] = dummy_cli
    sys.modules["mcp.server"] = types.SimpleNamespace(main=lambda: None)
    runpy.run_module("mcp.__main__", run_name="__main__")


def test_main_routes_to_server(monkeypatch):
    sys.modules.pop("mcp.__main__", None)
    monkeypatch.setattr(sys, "argv", ["-m", "mcp"])
    import types
    sys.modules["mcp.server"] = types.SimpleNamespace(main=lambda: None)
    runpy.run_module("mcp.__main__", run_name="__main__")
