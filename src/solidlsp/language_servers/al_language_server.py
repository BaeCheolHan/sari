import logging
import os
import pathlib
import platform
import re
import stat
import time
import zipfile
from pathlib import Path
import requests
from overrides import override
from solidlsp import ls_types
from solidlsp.language_servers.common import quote_windows_path
from solidlsp.ls import DocumentSymbols, LSPFileBuffer, SolidLanguageServer, get_current_process_env_snapshot
from solidlsp.ls_config import LanguageServerConfig
from solidlsp.ls_types import SymbolKind, UnifiedSymbolInformation
from solidlsp.lsp_protocol_handler.lsp_types import Definition, DefinitionParams, LocationLink
from solidlsp.lsp_protocol_handler.server import ProcessLaunchInfo
from solidlsp.settings import SolidLSPSettings
log = logging.getLogger(__name__)


def _env_snapshot() -> dict[str, str]:
    return get_current_process_env_snapshot()
class ALLanguageServer(SolidLanguageServer):
    _AL_OBJECT_NAME_PATTERN = re.compile(
        r"^(?:Table|Page|Codeunit|Enum|Interface|Report|Query|XMLPort|PermissionSet|"
        r"PermissionSetExtension|Profile|PageExtension|TableExtension|EnumExtension|"
        r"PageCustomization|ReportExtension|ControlAddin|DotNetPackage)"  # Object type
        r"(?:\s+\d+)?"  # Optional object ID
        r"\s+"  # Required space before name
        r'(?:"([^"]+)"|(\S+))$'  # Quoted name (group 1) or unquoted identifier (group 2)
    )
    @staticmethod
    def _extract_al_display_name(full_name: str) -> str:
        match = ALLanguageServer._AL_OBJECT_NAME_PATTERN.match(full_name)
        if match:
            return match.group(1) or match.group(2) or full_name
        return full_name
    def __init__(self, config: LanguageServerConfig, repository_root_path: str, solidlsp_settings: SolidLSPSettings):
        cmd = self._setup_runtime_dependencies(config, solidlsp_settings)
        self._project_load_check_supported: bool = True
        """Whether the AL server supports the project load status check request.
        Some AL server versions don't support the 'al/hasProjectClosureLoadedRequest'
        custom LSP request. This flag starts as True and is set to False if the
        request fails, preventing repeated unsuccessful attempts.
        """
        super().__init__(config, repository_root_path, ProcessLaunchInfo(cmd=cmd, cwd=repository_root_path), "al", solidlsp_settings)
        self._al_original_names: dict[tuple[str, int, int], str] = {}
    @staticmethod
    def _normalize_path(path: str) -> str:
        return path.replace("\\", "/")
    @classmethod
    def _download_al_extension(cls, url: str, target_dir: str) -> bool:
        try:
            log.info(f"Downloading AL extension from {url}")
            os.makedirs(target_dir, exist_ok=True)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/octet-stream, application/vsix, */*",
            }
            response = requests.get(url, headers=headers, stream=True, timeout=300)
            response.raise_for_status()
            temp_file = os.path.join(target_dir, "al_extension_temp.vsix")
            total_size = int(response.headers.get("content-length", 0))
            log.info(f"Downloading {total_size / 1024 / 1024:.1f} MB...")
            with open(temp_file, "wb") as f:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0 and downloaded % (10 * 1024 * 1024) == 0:  # Log progress every 10MB
                            progress = (downloaded / total_size) * 100
                            log.info(f"Download progress: {progress:.1f}%")
            log.info("Download complete, extracting...")
            with zipfile.ZipFile(temp_file, "r") as zip_ref:
                zip_ref.extractall(target_dir)
            os.remove(temp_file)
            log.info("AL extension extracted successfully")
            return True
        except (OSError, requests.RequestException, zipfile.BadZipFile, ValueError) as e:
            log.error(f"Error downloading/extracting AL extension: {e}")
            return False
    @classmethod
    def _setup_runtime_dependencies(cls, config: LanguageServerConfig, solidlsp_settings: SolidLSPSettings) -> str:
        system = platform.system()
        extension_path = cls._find_al_extension(solidlsp_settings)
        if extension_path is None:
            log.info("AL extension not found on disk, attempting to download...")
            extension_path = cls._download_and_install_al_extension(solidlsp_settings)
        if extension_path is None:
            raise RuntimeError(
                "Failed to locate or download AL Language Server. Please either:\n"
                "1. Set AL_EXTENSION_PATH environment variable to the AL extension directory\n"
                "2. Install the AL extension in VS Code (ms-dynamics-smb.al)\n"
                "3. Ensure internet connection for automatic download"
            )
        executable_path = cls._get_executable_path(extension_path, system)
        if not os.path.exists(executable_path):
            raise RuntimeError(f"AL Language Server executable not found at: {executable_path}")
        return cls._prepare_executable(executable_path, system)
    @classmethod
    def _find_al_extension(cls, solidlsp_settings: SolidLSPSettings) -> str | None:
        env_path = _env_snapshot().get("AL_EXTENSION_PATH")
        if env_path and os.path.exists(env_path):
            log.debug(f"Found AL extension via AL_EXTENSION_PATH: {env_path}")
            return env_path
        elif env_path:
            log.warning(f"AL_EXTENSION_PATH set but directory not found: {env_path}")
        default_path = os.path.join(cls.ls_resources_dir(solidlsp_settings), "al-extension", "extension")
        if os.path.exists(default_path):
            log.debug(f"Found AL extension in default location: {default_path}")
            return default_path
        vscode_path = cls._find_al_extension_in_vscode()
        if vscode_path:
            log.debug(f"Found AL extension in VS Code: {vscode_path}")
            return vscode_path
        log.debug("AL extension not found in any known location")
        return None
    @classmethod
    def _download_and_install_al_extension(cls, solidlsp_settings: SolidLSPSettings) -> str | None:
        al_extension_dir = os.path.join(cls.ls_resources_dir(solidlsp_settings), "al-extension")
        AL_VERSION = "latest"
        url = f"https://marketplace.visualstudio.com/_apis/public/gallery/publishers/ms-dynamics-smb/vsextensions/al/{AL_VERSION}/vspackage"
        log.info(f"Downloading AL extension from: {url}")
        if cls._download_al_extension(url, al_extension_dir):
            extension_path = os.path.join(al_extension_dir, "extension")
            if os.path.exists(extension_path):
                log.info("AL extension downloaded and installed successfully")
                return extension_path
            else:
                log.error(f"Download completed but extension not found at: {extension_path}")
        else:
            log.error("Failed to download AL extension from marketplace")
        return None
    @classmethod
    def _get_executable_path(cls, extension_path: str, system: str) -> str:
        if system == "Windows":
            return os.path.join(extension_path, "bin", "win32", "Microsoft.Dynamics.Nav.EditorServices.Host.exe")
        elif system == "Linux":
            return os.path.join(extension_path, "bin", "linux", "Microsoft.Dynamics.Nav.EditorServices.Host")
        elif system == "Darwin":
            return os.path.join(extension_path, "bin", "darwin", "Microsoft.Dynamics.Nav.EditorServices.Host")
        else:
            raise RuntimeError(f"Unsupported platform: {system}")
    @classmethod
    def _prepare_executable(cls, executable_path: str, system: str) -> str:
        if system in ["Linux", "Darwin"]:
            st = os.stat(executable_path)
            os.chmod(executable_path, st.st_mode | stat.S_IEXEC)
            log.debug(f"Set execute permission on: {executable_path}")
        log.info(f"Using AL Language Server executable: {executable_path}")
        return quote_windows_path(executable_path)
    @classmethod
    def _get_language_server_command_fallback(cls) -> str:
        al_extension_path = _env_snapshot().get("AL_EXTENSION_PATH")
        if not al_extension_path:
            cwd_path = Path.cwd()
            potential_extension = None
            for item in cwd_path.iterdir():
                if item.is_dir() and item.name.startswith("ms-dynamics-smb.al-"):
                    potential_extension = item
                    break
            if potential_extension:
                al_extension_path = str(potential_extension)
                log.debug(f"Found AL extension in current directory: {al_extension_path}")
            else:
                al_extension_path = cls._find_al_extension_in_vscode()
        if not al_extension_path:
            raise RuntimeError(
                "AL Language Server not found. Please either:\n"
                "1. Set AL_EXTENSION_PATH environment variable to the VS Code AL extension directory\n"
                "2. Install the AL extension in VS Code (ms-dynamics-smb.al)\n"
                "3. Place the extension directory in the current working directory"
            )
        system = platform.system()
        if system == "Windows":
            executable = os.path.join(al_extension_path, "bin", "win32", "Microsoft.Dynamics.Nav.EditorServices.Host.exe")
        elif system == "Linux":
            executable = os.path.join(al_extension_path, "bin", "linux", "Microsoft.Dynamics.Nav.EditorServices.Host")
        elif system == "Darwin":
            executable = os.path.join(al_extension_path, "bin", "darwin", "Microsoft.Dynamics.Nav.EditorServices.Host")
        else:
            raise RuntimeError(f"Unsupported platform: {system}")
        if not os.path.exists(executable):
            raise RuntimeError(
                f"AL Language Server executable not found at: {executable}\nPlease ensure the AL extension is properly installed."
            )
        if system in ["Linux", "Darwin"]:
            st = os.stat(executable)
            os.chmod(executable, st.st_mode | stat.S_IEXEC)
        log.info(f"Using AL Language Server executable: {executable}")
        return quote_windows_path(executable)
    @classmethod
    def _find_al_extension_in_vscode(cls) -> str | None:
        home = Path.home()
        possible_paths = []
        if platform.system() == "Windows":
            possible_paths.extend(
                [
                    home / ".vscode" / "extensions",
                    home / ".vscode-insiders" / "extensions",
                    Path(_env_snapshot().get("APPDATA", "")) / "Code" / "User" / "extensions",
                    Path(_env_snapshot().get("APPDATA", "")) / "Code - Insiders" / "User" / "extensions",
                ]
            )
        else:
            possible_paths.extend(
                [
                    home / ".vscode" / "extensions",
                    home / ".vscode-server" / "extensions",
                    home / ".vscode-insiders" / "extensions",
                ]
            )
        for base_path in possible_paths:
            if base_path.exists():
                log.debug(f"Searching for AL extension in: {base_path}")
                for item in base_path.iterdir():
                    if item.is_dir() and item.name.startswith("ms-dynamics-smb.al-"):
                        log.debug(f"Found AL extension at: {item}")
                        return str(item)
        return None
    @staticmethod
    def _get_initialize_params(repository_absolute_path: str) -> dict:
        repository_path = pathlib.Path(repository_absolute_path).resolve()
        root_uri = repository_path.as_uri()
        initialize_params = {
            "processId": os.getpid(),
            "rootPath": str(repository_path),
            "rootUri": root_uri,
            "capabilities": {
                "workspace": {
                    "applyEdit": True,
                    "workspaceEdit": {
                        "documentChanges": True,
                        "resourceOperations": ["create", "rename", "delete"],
                        "failureHandling": "textOnlyTransactional",
                        "normalizesLineEndings": True,
                    },
                    "configuration": True,
                    "didChangeWatchedFiles": {"dynamicRegistration": True},
                    "symbol": {"dynamicRegistration": True, "symbolKind": {"valueSet": list(range(1, 27))}},
                    "executeCommand": {"dynamicRegistration": True},
                    "didChangeConfiguration": {"dynamicRegistration": True},
                    "workspaceFolders": True,
                },
                "textDocument": {
                    "synchronization": {"dynamicRegistration": True, "willSave": True, "willSaveWaitUntil": True, "didSave": True},
                    "completion": {
                        "dynamicRegistration": True,
                        "contextSupport": True,
                        "completionItem": {
                            "snippetSupport": True,
                            "commitCharactersSupport": True,
                            "documentationFormat": ["markdown", "plaintext"],
                            "deprecatedSupport": True,
                            "preselectSupport": True,
                        },
                    },
                    "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
                    "definition": {"dynamicRegistration": True, "linkSupport": True},
                    "references": {"dynamicRegistration": True},
                    "documentHighlight": {"dynamicRegistration": True},
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "symbolKind": {"valueSet": list(range(1, 27))},
                        "hierarchicalDocumentSymbolSupport": True,
                    },
                    "codeAction": {"dynamicRegistration": True},
                    "formatting": {"dynamicRegistration": True},
                    "rangeFormatting": {"dynamicRegistration": True},
                    "rename": {"dynamicRegistration": True, "prepareSupport": True},
                },
                "window": {
                    "showMessage": {"messageActionItem": {"additionalPropertiesSupport": True}},
                    "showDocument": {"support": True},
                    "workDoneProgress": True,
                },
            },
            "trace": "verbose",
            "workspaceFolders": [{"uri": root_uri, "name": repository_path.name}],
        }
        return initialize_params
    @override
    def _start_server(self) -> None:
        def do_nothing(params: str) -> None:
            return
        def window_log_message(msg: dict) -> None:
            log.info(f"AL LSP: window/logMessage: {msg}")
        def publish_diagnostics(params: dict) -> None:
            uri = params.get("uri", "")
            diagnostics = params.get("diagnostics", [])
            log.debug(f"AL LSP: Diagnostics for {uri}: {len(diagnostics)} issues")
        def handle_al_notifications(params: dict) -> None:
            log.debug("AL LSP: Notification received")
        self.server.on_notification("window/logMessage", window_log_message)  # Server log messages
        self.server.on_notification("textDocument/publishDiagnostics", publish_diagnostics)  # Compilation diagnostics
        self.server.on_notification("$/progress", do_nothing)  # Progress notifications during loading
        self.server.on_notification("al/refreshExplorerObjects", handle_al_notifications)  # AL-specific object updates
        log.info("Starting AL Language Server process")
        self.server.start()
        initialize_params = self._get_initialize_params(self.repository_root_path)
        log.info("Sending initialize request from LSP client to AL LSP server and awaiting response")
        resp = self.server.send_request("initialize", initialize_params)
        if resp is None:
            raise RuntimeError("AL Language Server initialization failed - no response")
        log.info("AL Language Server initialized successfully")
        self.server.send_notification("initialized", {})
        log.info("Sent initialized notification")
    @override
    def start(self) -> "ALLanguageServer":
        super().start()
        self._post_initialize_al_workspace()
        return self
    def _post_initialize_al_workspace(self) -> None:
        try:
            self.server.send_notification(
                "workspace/didChangeConfiguration",
                {
                    "settings": {
                        "workspacePath": self.repository_root_path,
                        "alResourceConfigurationSettings": {
                            "assemblyProbingPaths": ["./.netpackages"],
                            "codeAnalyzers": [],
                            "enableCodeAnalysis": False,
                            "backgroundCodeAnalysis": "Project",
                            "packageCachePaths": ["./.alpackages"],
                            "ruleSetPath": None,
                            "enableCodeActions": True,
                            "incrementalBuild": False,
                            "outputAnalyzerStatistics": True,
                            "enableExternalRulesets": True,
                        },
                        "setActiveWorkspace": True,
                        "expectedProjectReferenceDefinitions": [],
                        "activeWorkspaceClosure": [self.repository_root_path],
                    }
                },
            )
            log.debug("Sent workspace configuration")
        except (RuntimeError, ValueError, OSError) as e:
            log.warning(f"Failed to send workspace config: {e}")
        app_json_path = Path(self.repository_root_path) / "app.json"
        if app_json_path.exists():
            try:
                with open(app_json_path, encoding="utf-8") as f:
                    app_json_content = f.read()
                app_json_uri = app_json_path.as_uri()
                self.server.send_notification(
                    "textDocument/didOpen",
                    {"textDocument": {"uri": app_json_uri, "languageId": "json", "version": 1, "text": app_json_content}},
                )
                log.debug(f"Opened app.json: {app_json_uri}")
            except (OSError, ValueError) as e:
                log.warning(f"Failed to open app.json: {e}")
        workspace_uri = Path(self.repository_root_path).resolve().as_uri()
        try:
            result = self.server.send_request(
                "al/setActiveWorkspace",
                {
                    "currentWorkspaceFolderPath": {"uri": workspace_uri, "name": Path(self.repository_root_path).name, "index": 0},
                    "settings": {
                        "workspacePath": self.repository_root_path,
                        "setActiveWorkspace": True,
                    },
                    "timeout": 2,  # Quick timeout since this is optional
                },
            )
            log.debug(f"Set active workspace result: {result}")
        except (RuntimeError, ValueError, OSError) as e:
            log.debug(f"Failed to set active workspace (non-critical): {e}")
        self._wait_for_project_load(timeout=3)
    @override
    def is_ignored_dirname(self, dirname: str) -> bool:
        al_ignore_dirs = {
            ".alpackages",  # AL package cache - downloaded dependencies
            ".alcache",  # AL compiler cache - intermediate compilation files
            ".altemplates",  # AL templates - code generation templates
            ".snapshots",  # Test snapshots - test result snapshots
            "out",  # Compiled output - generated .app files
            ".vscode",  # VS Code settings - editor configuration
            "Reference",  # Reference assemblies - .NET dependencies
            ".netpackages",  # .NET packages - NuGet packages for AL
            "bin",  # Binary output - compiled binaries
            "obj",  # Object files - intermediate build artifacts
        }
        return super().is_ignored_dirname(dirname) or dirname in al_ignore_dirs
    @override
    def request_full_symbol_tree(self, within_relative_path: str | None = None) -> list[UnifiedSymbolInformation]:
        log.debug("AL: Starting request_full_symbol_tree with file opening")
        if within_relative_path is not None:
            within_abs_path = os.path.join(self.repository_root_path, within_relative_path)
            if not os.path.exists(within_abs_path):
                raise FileNotFoundError(f"File or directory not found: {within_abs_path}")
            if os.path.isfile(within_abs_path):
                root_nodes = self.request_document_symbols(within_relative_path).root_symbols
                return root_nodes
            scan_root = Path(within_abs_path)
        else:
            scan_root = Path(self.repository_root_path)
        al_files = []
        for root, dirs, files in os.walk(scan_root):
            dirs[:] = [d for d in dirs if not self.is_ignored_dirname(d)]
            for file in files:
                if file.endswith((".al", ".dal")):
                    file_path = Path(root) / file
                    try:
                        relative_path = str(file_path.relative_to(self.repository_root_path)).replace("\\", "/")
                        al_files.append((file_path, relative_path))
                    except ValueError:
                        continue
        log.debug(f"AL: Found {len(al_files)} AL files")
        if not al_files:
            log.warning("AL: No AL files found in repository")
            return []
        all_file_symbols: list[UnifiedSymbolInformation] = []
        file_symbol: UnifiedSymbolInformation
        for file_path, relative_path in al_files:
            try:
                log.debug(f"AL: Getting symbols for {relative_path}")
                all_syms, root_syms = self.request_document_symbols(relative_path).get_all_symbols_and_roots()
                if root_syms:
                    file_symbol = {
                        "name": file_path.stem,  # Just the filename without extension
                        "kind": SymbolKind.File,
                        "children": root_syms,
                        "location": {
                            "uri": file_path.as_uri(),
                            "relativePath": relative_path,
                            "absolutePath": str(file_path),
                            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
                        },
                    }
                    all_file_symbols.append(file_symbol)
                    log.debug(f"AL: Added {len(root_syms)} symbols from {relative_path}")
                elif all_syms:
                    file_symbol = {
                        "name": file_path.stem,
                        "kind": SymbolKind.File,
                        "children": all_syms,
                        "location": {
                            "uri": file_path.as_uri(),
                            "relativePath": relative_path,
                            "absolutePath": str(file_path),
                            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
                        },
                    }
                    all_file_symbols.append(file_symbol)
                    log.debug(f"AL: Added {len(all_syms)} symbols from {relative_path}")
            except (RuntimeError, ValueError, OSError) as e:
                log.warning(f"AL: Failed to get symbols for {relative_path}: {e}")
        if all_file_symbols:
            log.debug(f"AL: Returning symbols from {len(all_file_symbols)} files")
            directory_structure: dict[str, list] = {}
            for file_symbol in all_file_symbols:
                rel_path = file_symbol["location"]["relativePath"]
                assert rel_path is not None
                path_parts = rel_path.split("/")
                if len(path_parts) > 1:
                    dir_path = "/".join(path_parts[:-1])
                    if dir_path not in directory_structure:
                        directory_structure[dir_path] = []
                    directory_structure[dir_path].append(file_symbol)
                else:
                    if "." not in directory_structure:
                        directory_structure["."] = []
                    directory_structure["."].append(file_symbol)
            result = []
            repo_path = Path(self.repository_root_path)
            for dir_path, file_symbols in directory_structure.items():
                if dir_path == ".":
                    result.extend(file_symbols)
                else:
                    dir_symbol = {
                        "name": Path(dir_path).name,
                        "kind": SymbolKind.Package,  # Package/Directory
                        "children": file_symbols,
                        "location": {
                            "relativePath": dir_path,
                            "absolutePath": str(repo_path / dir_path),
                            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
                        },
                    }
                    result.append(dir_symbol)
            return result
        else:
            log.warning("AL: No symbols found in any files")
            return []
    @override
    def _send_definition_request(self, definition_params: DefinitionParams) -> Definition | list[LocationLink] | None:
        al_params = {"textDocument": definition_params["textDocument"], "position": definition_params["position"]}
        try:
            response = self.server.send_request("al/gotodefinition", al_params)
            log.debug(f"AL gotodefinition response: {response}")
            return response  # type: ignore[return-value]
        except (RuntimeError, ValueError, OSError) as e:
            log.warning(f"Failed to use al/gotodefinition, falling back to standard: {e}")
            return super()._send_definition_request(definition_params)
    def check_project_loaded(self) -> bool:
        if not hasattr(self, "server") or not self.server_started:
            log.debug("Cannot check project load - server not started")
            return False
        if not self._project_load_check_supported:
            return True  # Assume loaded if check isn't supported
        try:
            response = self.server.send_request("al/hasProjectClosureLoadedRequest", {"timeout": 1})
            if isinstance(response, bool):
                return response
            elif isinstance(response, dict):
                return response.get("loaded", False)
            elif response is None:
                log.debug("Project load check returned None")
                return False
            else:
                log.debug(f"Unexpected response type for project load check: {type(response)}")
                return False
        except (RuntimeError, ValueError, OSError, TypeError) as e:
            self._project_load_check_supported = False
            log.debug(f"Project load check not supported by this AL server version: {e}")
            return True
    def _wait_for_project_load(self, timeout: int = 3) -> bool:
        start_time = time.time()
        log.debug(f"Checking AL project load status (timeout: {timeout}s)...")
        while time.time() - start_time < timeout:
            if self.check_project_loaded():
                elapsed = time.time() - start_time
                log.info(f"AL project fully loaded after {elapsed:.1f}s")
                return True
            time.sleep(0.5)
        log.debug(f"Project load check timed out after {timeout}s (non-critical)")
        return False
    def set_active_workspace(self, workspace_uri: str | None = None) -> None:
        if not hasattr(self, "server") or not self.server_started:
            log.debug("Cannot set active workspace - server not started")
            return
        if workspace_uri is None:
            workspace_uri = Path(self.repository_root_path).resolve().as_uri()
        params = {"workspaceUri": workspace_uri}
        try:
            self.server.send_request("al/setActiveWorkspace", params)
            log.info(f"Set active workspace to: {workspace_uri}")
        except (RuntimeError, ValueError, OSError) as e:
            log.warning(f"Failed to set active workspace: {e}")
    @override
    def request_document_symbols(
        self,
        relative_file_path: str,
        file_buffer: LSPFileBuffer | None = None,
        *,
        sync_with_ls: bool = True,
    ) -> DocumentSymbols:
        relative_file_path = self._normalize_path(relative_file_path)
        document_symbols = super().request_document_symbols(
            relative_file_path,
            file_buffer=file_buffer,
            sync_with_ls=sync_with_ls,
        )
        def normalize_name(symbol: UnifiedSymbolInformation) -> None:
            original_name = symbol["name"]
            normalized_name = self._extract_al_display_name(original_name)
            if original_name != normalized_name:
                sel_range = symbol.get("selectionRange")
                if sel_range:
                    start = sel_range.get("start")
                    if start and "line" in start and "character" in start:
                        line = start["line"]
                        char = start["character"]
                        self._al_original_names[(relative_file_path, line, char)] = original_name
            symbol["name"] = normalized_name
            if symbol.get("children"):
                for child in symbol["children"]:
                    normalize_name(child)
        for sym in document_symbols.root_symbols:
            normalize_name(sym)
        return document_symbols
    @override
    def request_hover(self, relative_file_path: str, line: int, column: int) -> ls_types.Hover | None:
        relative_file_path = self._normalize_path(relative_file_path)
        hover = super().request_hover(relative_file_path, line, column)
        if hover is None:
            return None
        original_name = self._al_original_names.get((relative_file_path, line, column))
        if original_name and "contents" in hover:
            contents = hover["contents"]
            if isinstance(contents, dict) and "value" in contents:
                prefix = f"**{original_name}**\n\n---\n\n"
                contents["value"] = prefix + contents["value"]
        return hover
