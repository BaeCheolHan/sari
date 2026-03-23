from __future__ import annotations

import os
import pathlib
import time
from unittest.mock import MagicMock, patch

import pytest

from solidlsp.language_servers.pyrefly_server import PyreflyServer
from solidlsp.language_servers.pyright_server import PyrightServer
from solidlsp.ls_config import LanguageServerConfig, Language
from solidlsp.settings import SolidLSPSettings


@pytest.fixture
def repo_root():
    return str(pathlib.Path(__file__).parent.parent.parent.parent.absolute())

@pytest.fixture
def mock_settings():
    return SolidLSPSettings()

def create_server(server_class, config, repo_root, settings):
    # This might need adjustment based on how the servers are actually instantiated
    # in the production code to avoid side effects.
    return server_class(config, repo_root, settings)

@pytest.mark.skipif(not os.environ.get("SARI_RUN_LSP_ACCURACY_TEST"), reason="Expensive LSP accuracy test")
def test_compare_accuracy_on_status_endpoint(repo_root, mock_settings):
    """
    Compare accuracy of Pyright vs Pyrefly on 'status_endpoint' references.
    Expected: Pyright finds ~5. Can Pyrefly find same or more with the new config?
    """
    with patch("shutil.which", return_value="/usr/bin/pyrefly"):
        # 1. Setup Pyright
        pyright_config = LanguageServerConfig(code_language=Language.PYTHON)
        pyright_server = PyrightServer(pyright_config, repo_root, mock_settings)
        
        # 2. Setup Pyrefly with 'SARI_PYTHON_LSP_PROVIDER=pyrefly'
        os.environ["SARI_PYTHON_LSP_PROVIDER"] = "pyrefly"
        pyrefly_config = LanguageServerConfig(code_language=Language.PYTHON)
        pyrefly_server = PyreflyServer(pyrefly_config, repo_root, mock_settings)
        
        # Assert Pyrefly initialization includes our new options
        params = pyrefly_server._get_initialize_params(repo_root)
        assert params["initializationOptions"]["python"]["pyrefly"]["displayTypeErrors"] == "force-on"
        assert params["initializationOptions"]["python"]["pyrefly"]["analyzer"] is True
        assert params["initializationOptions"]["python"]["analysis"]["mode"] == "full"
