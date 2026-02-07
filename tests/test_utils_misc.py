import pytest
import logging
import os
from sari.core.utils.compression import _compress, _decompress
from sari.core.utils.logging import get_logger, configure_logging
from sari.core.workspace import WorkspaceManager

def test_compression():
    text = "Hello World" * 100
    compressed = _compress(text)
    assert isinstance(compressed, bytes)
    assert len(compressed) < len(text.encode("utf-8"))
    
    decompressed = _decompress(compressed)
    assert decompressed == text
    
    assert _compress("") == b""
    assert _decompress(b"") == ""
    assert _decompress("already string") == "already string"
    assert _decompress(b"invalid data") == str(b"invalid data")

def test_get_logger():
    # New get_logger returns a structlog bound logger
    logger = get_logger("test_logger")
    # Verify it has logging methods
    assert hasattr(logger, "info")
    assert hasattr(logger, "error")

def test_configure_logging():
    # This calls structlog.configure
    try:
        configure_logging()
    except Exception as e:
        pytest.fail(f"configure_logging raised exception: {e}")


def test_workspace_normalize_path_never_returns_empty_for_root():
    assert WorkspaceManager.normalize_path("/") == "/"


def test_workspace_normalize_path_empty_falls_back_to_cwd():
    out = WorkspaceManager.normalize_path("")
    assert isinstance(out, str)
    assert out != ""
