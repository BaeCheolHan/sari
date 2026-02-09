import pytest
import json
from sari.core.parsers.ast_engine import ASTEngine

def test_dockerfile_insight():
    """
    Verify that Dockerfile instructions are captured.
    """
    engine = ASTEngine()
    code = (
        "FROM python:3.9-slim\n"
        "ENV SARI_HOME=/app\n"
        "EXPOSE 47800\n"
        "CMD [\"python\", \"-m\", \"sari\"]\n"
    )
    # Correct unpack for standardized return type
    symbols, _ = engine.extract_symbols("Dockerfile", "dockerfile", code)
    assert any(s.name == "FROM" for s in symbols)
    assert any(s.name == "EXPOSE" for s in symbols)
    print(f"\nDEBUG: Docker SUCCESS. Found: {[s.name for s in symbols]}")

def test_markdown_navigation():
    """
    Verify that Markdown headers are extracted for navigation.
    """
    engine = ASTEngine()
    code = (
        "# Sari Project\n"
        "## Core Architecture\n"
        "### Ultra Turbo Engine\n"
    )
    symbols, _ = engine.extract_symbols("README.md", "markdown", code)
    assert any(s.name == "Sari Project" for s in symbols)
    assert any(s.name == "Core Architecture" for s in symbols)
    print(f"DEBUG: Markdown SUCCESS. Found headers: {[s.name for s in symbols]}")

def test_bash_script_logic():
    """
    Verify that Shell script functions and variables are captured.
    """
    engine = ASTEngine()
    code = (
        "#!/bin/bash\n"
        "SARI_PORT=47800\n"
        "function start_daemon() {\n"
        "  echo 'Starting...'\n"
        "}\n"
    )
    symbols, _ = engine.extract_symbols("bootstrap.sh", "bash", code)
    assert any(s.name == "start_daemon" and s.kind == "method" for s in symbols)
    assert any(s.name == "SARI_PORT" and s.kind == "variable" for s in symbols)
    print(f"DEBUG: Bash SUCCESS. Found: {[s.name for s in symbols]}")