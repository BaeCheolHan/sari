"""
Provides Python specific instantiation of the LanguageServer class using Pyrefly.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import time
from typing import cast

from overrides import override

from solidlsp.language_servers._adapter_common import ensure_commands_available
from solidlsp.ls import LanguageServerDependencyProvider, LanguageServerDependencyProviderSinglePath, SolidLanguageServer
from solidlsp.ls_config import LanguageServerConfig
from solidlsp.ls_exceptions import SolidLSPException
from solidlsp.lsp_protocol_handler.lsp_types import InitializeParams
from solidlsp.settings import SolidLSPSettings


class PyreflyServer(SolidLanguageServer):
    """Provides Python specific instantiation of the LanguageServer class using Pyrefly."""

    _SUBSEQUENT_MUTATION_RETRY_SLEEP_SEC = 0.25

    def __init__(self, config: LanguageServerConfig, repository_root_path: str, solidlsp_settings: SolidLSPSettings):
        super().__init__(
            config,
            repository_root_path,
            None,
            "python",
            solidlsp_settings,
        )
        self._primed_reference_paths: set[str] = set()

    def _create_dependency_provider(self) -> LanguageServerDependencyProvider:
        return self.DependencyProvider(self._custom_settings, self._ls_resources_dir)

    def _requires_open_file_for_document_symbols(self) -> bool:
        return False

    def request_document_symbols(
        self,
        relative_file_path: str,
        file_buffer=None,
        *,
        sync_with_ls: bool = True,
    ):
        try:
            return super().request_document_symbols(
                relative_file_path,
                file_buffer=file_buffer,
                sync_with_ls=sync_with_ls,
            )
        except SolidLSPException as exc:
            message = str(exc)
            if "subsequent mutation (-32800)" not in message:
                raise
            time.sleep(self._SUBSEQUENT_MUTATION_RETRY_SLEEP_SEC)
            return super().request_document_symbols(
                relative_file_path,
                file_buffer=file_buffer,
                sync_with_ls=sync_with_ls,
            )

    def request_references(self, relative_file_path: str, line: int, column: int):
        primed_paths = getattr(self, "_primed_reference_paths", None)
        if primed_paths is None:
            primed_paths = set()
            self._primed_reference_paths = primed_paths
        if relative_file_path not in primed_paths:
            self.request_document_symbols(relative_file_path)
            primed_paths.add(relative_file_path)
        try:
            return super().request_references(relative_file_path, line, column)
        except SolidLSPException as exc:
            message = str(exc)
            if "subsequent mutation (-32800)" not in message:
                raise
            time.sleep(self._SUBSEQUENT_MUTATION_RETRY_SLEEP_SEC)
            return super().request_references(relative_file_path, line, column)

    class DependencyProvider(LanguageServerDependencyProviderSinglePath):
        def _get_or_install_core_dependency(self) -> str:
            ensure_commands_available(["pyrefly"])
            resolved = shutil.which("pyrefly", path=self.env_get("PATH"))
            if resolved is None:
                raise RuntimeError("missing required commands: pyrefly")
            return resolved

        def _create_launch_command(self, core_path: str) -> list[str]:
            command = [core_path, "lsp"]
            indexing_mode = (self.env_get("SARI_PYREFLY_INDEXING_MODE", "") or "").strip()
            if indexing_mode != "":
                command.extend(["--indexing-mode", indexing_mode])
            return command

    @override
    def is_ignored_dirname(self, dirname: str) -> bool:
        return super().is_ignored_dirname(dirname) or dirname in ["venv", "__pycache__"]

    @staticmethod
    def _get_initialize_params(repository_absolute_path: str) -> InitializeParams:
        analysis_mode = os.environ.get("SARI_PYREFLY_ANALYSIS_MODE", "full").strip()
        indexing_mode = os.environ.get("SARI_PYREFLY_INDEXING_MODE", "incremental").strip()

        initialize_params = {  # type: ignore
            "processId": os.getpid(),
            "rootPath": repository_absolute_path,
            "rootUri": pathlib.Path(repository_absolute_path).as_uri(),
            "initializationOptions": {
                "python": {
                    "pyrefly": {
                        "displayTypeErrors": "force-on",
                        "analyzer": True,
                    },
                    "analysis": {
                        "mode": analysis_mode,
                        "indexing": True,
                    }
                }
            },
            "capabilities": {
                "workspace": {
                    "workspaceEdit": {"documentChanges": True},
                    "didChangeConfiguration": {"dynamicRegistration": True},
                    "didChangeWatchedFiles": {"dynamicRegistration": True},
                    "symbol": {
                        "dynamicRegistration": True,
                        "symbolKind": {"valueSet": list(range(1, 27))},
                    },
                    "executeCommand": {"dynamicRegistration": True},
                },
                "textDocument": {
                    "synchronization": {"dynamicRegistration": True, "willSave": True, "willSaveWaitUntil": True, "didSave": True},
                    "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
                    "signatureHelp": {
                        "dynamicRegistration": True,
                        "signatureInformation": {
                            "documentationFormat": ["markdown", "plaintext"],
                            "parameterInformation": {"labelOffsetSupport": True},
                        },
                    },
                    "definition": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "symbolKind": {"valueSet": list(range(1, 27))},
                        "hierarchicalDocumentSymbolSupport": True,
                    },
                    "publishDiagnostics": {"relatedInformation": True},
                },
            },
            "workspaceFolders": [
                {"uri": pathlib.Path(repository_absolute_path).as_uri(), "name": os.path.basename(repository_absolute_path)}
            ],
        }
        return cast(InitializeParams, initialize_params)

    def _start_server(self) -> None:
        # Register client-side handlers for requests initiated by the server
        self.server.on_request("client/registerCapability", lambda params: None)
        self.server.on_request("client/unregisterCapability", lambda params: None)

        def handle_config(params):
            items = params.get("items", [])
            return [{} for _ in items]

        def handle_folders(params):
            return [
                {
                    "uri": pathlib.Path(self.repository_root_path).as_uri(),
                    "name": os.path.basename(self.repository_root_path),
                }
            ]

        self.server.on_request("workspace/configuration", handle_config)
        self.server.on_request("workspace/workspaceFolders", handle_folders)

        self.server.start()
        init_response = self.server.send.initialize(self._get_initialize_params(self.repository_root_path))
        assert "capabilities" in init_response
        self.server.notify.initialized({})
