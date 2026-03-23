from __future__ import annotations

import os
import pathlib
import logging
import re
import shutil
import subprocess
from typing import cast

from overrides import override

from solidlsp.ls_config import LanguageServerConfig
from solidlsp.lsp_protocol_handler.lsp_types import InitializeParams, Location
from solidlsp.ls import SolidLanguageServer, SolidLSPSettings, LanguageServerDependencyProvider
from solidlsp.ls_utils import PlatformUtils, FileUtils

log = logging.getLogger(__name__)


class JavaLightServer(SolidLanguageServer):
    """
    georgewfraser/java-language-server 기반의 경량 Java LSP 구현체.
    """

    def __init__(
        self,
        config: LanguageServerConfig,
        repository_root_path: str,
        solidlsp_settings: SolidLSPSettings,
    ) -> None:
        super().__init__(config, repository_root_path, None, "java", solidlsp_settings)
        self.repository_root_path = os.path.abspath(repository_root_path)

    @classmethod
    def get_language_enum_instance(cls):
        from solidlsp.ls_config import Language

        return Language.JAVA

    @override
    def _create_dependency_provider(self) -> LanguageServerDependencyProvider:
        ls_resources_dir = self.ls_resources_dir(self._solidlsp_settings)
        return self.DependencyProvider(self._custom_settings, ls_resources_dir, self.repository_root_path)

    class DependencyProvider(LanguageServerDependencyProvider):
        def __init__(
            self,
            custom_settings: SolidLSPSettings.CustomLSSettings,
            ls_resources_dir: str,
            repository_root_path: str | None = None,
        ):
            super().__init__(custom_settings, ls_resources_dir)
            self.runtime_dependencies = self._get_jdk21_definitions()
            self._resolved_java_home: str | None = None
            self._resolved_java_bin: str | None = None
            self._resolved_java_version: int | None = None
            self._repository_root_path = repository_root_path or os.getcwd()
            self._required_java_version = self._detect_required_java_version()

        def _get_jdk21_definitions(self) -> dict:
            return {
                "jdk21": {
                    "osx-arm64": {
                        "url": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.2%2B13/OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.2_13.tar.gz",
                        "archiveType": "tar.gz",
                        "relative_extraction_path": "java21",
                    },
                    "osx-x64": {
                        "url": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.2%2B13/OpenJDK21U-jdk_x64_mac_hotspot_21.0.2_13.tar.gz",
                        "archiveType": "tar.gz",
                        "relative_extraction_path": "java21",
                    },
                    "linux-x64": {
                        "url": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.2%2B13/OpenJDK21U-jdk_x64_linux_hotspot_21.0.2_13.tar.gz",
                        "archiveType": "tar.gz",
                        "relative_extraction_path": "java21",
                    },
                    "win-x64": {
                        "url": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.2%2B13/OpenJDK21U-jdk_x64_windows_hotspot_21.0.2_13.zip",
                        "archiveType": "zip",
                        "relative_extraction_path": "java21",
                    }
                }
            }

        def _normalize_archive_type(self, archive_type: str) -> str:
            if archive_type == "tar.gz":
                return "gztar"
            return archive_type

        @override
        def create_launch_command(self) -> list[str] | str:
            java_bin, java_home = self._resolve_java_runtime()
            self._resolved_java_home = java_home
            self._resolved_java_bin = java_bin

            # 2. Server JAR discovery
            javalight_home = os.environ.get("SARI_JAVALIGHT_HOME")
            if not javalight_home:
                javalight_home = self._managed_server_home()
                if not self._has_server_classpath(javalight_home):
                    self._bootstrap_managed_server_home(javalight_home)

            if not javalight_home or not self._has_server_classpath(javalight_home):
                error_msg = (
                    "JavaLight binary not found. Set SARI_JAVALIGHT_HOME or provide a bootstrap source via "
                    "SARI_JAVALIGHT_BOOTSTRAP_HOME. JavaLight is currently an optional high-performance engine."
                )
                log.error(error_msg)
                raise RuntimeError(error_msg)

            dist_cp = os.path.join(javalight_home, "dist", "classpath")
            classpath = os.path.join(dist_cp, "*")
            
            return [
                java_bin, "-cp", classpath,
                "--add-exports", "jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED",
                "--add-exports", "jdk.compiler/com.sun.tools.javac.code=ALL-UNNAMED",
                "--add-exports", "jdk.compiler/com.sun.tools.javac.comp=ALL-UNNAMED",
                "--add-exports", "jdk.compiler/com.sun.tools.javac.main=ALL-UNNAMED",
                "--add-exports", "jdk.compiler/com.sun.tools.javac.tree=ALL-UNNAMED",
                "--add-exports", "jdk.compiler/com.sun.tools.javac.model=ALL-UNNAMED",
                "--add-exports", "jdk.compiler/com.sun.tools.javac.util=ALL-UNNAMED",
                "--add-opens", "jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED",
                "org.javacs.Main"
            ]

        @override
        def create_launch_command_env(self) -> dict[str, str]:
            if self._resolved_java_home is None:
                return {}
            return {"JAVA_HOME": self._resolved_java_home}

        def _managed_server_home(self) -> str:
            return os.path.join(self._ls_resources_dir, "managed-javalight")

        def _managed_jdk_home(self) -> str:
            platform_id = PlatformUtils.get_platform_id()
            jdk_info = self.runtime_dependencies["jdk21"].get(platform_id)
            if jdk_info is None:
                return self._ls_resources_dir
            return os.path.join(self._ls_resources_dir, jdk_info["relative_extraction_path"])

        def _ensure_managed_jdk_home(self) -> str:
            platform_id = PlatformUtils.get_platform_id()
            jdk_info = self.runtime_dependencies["jdk21"].get(platform_id)
            if jdk_info is None:
                raise RuntimeError(f"managed JDK is not configured for platform: {platform_id}")
            jdk_dir = os.path.join(self._ls_resources_dir, jdk_info["relative_extraction_path"])
            java_bin = self._find_java_binary(jdk_dir)
            if java_bin != "java" and self._java_supports_compiler_modules(java_bin):
                return jdk_dir
            log.info(f"Downloading managed JDK 21 for {platform_id}...")
            FileUtils.download_and_extract_archive(
                jdk_info["url"],
                jdk_dir,
                archive_type=self._normalize_archive_type(jdk_info["archiveType"]),
            )
            return jdk_dir

        def _resolve_java_runtime(self) -> tuple[str, str | None]:
            candidates: list[tuple[str, str | None | object]] = []
            explicit_java_home = self.env_get("SARI_JAVALIGHT_JAVA_HOME")
            if explicit_java_home:
                candidates.append(("explicit_java_home", explicit_java_home))
            env_java_home = self.env_get("JAVA_HOME")
            if env_java_home:
                candidates.append(("java_home", env_java_home))
            candidates.append(("managed_jdk", object()))
            candidates.append(("system_java", None))

            for source, home_candidate in candidates:
                home = self._ensure_managed_jdk_home() if source == "managed_jdk" else home_candidate
                java_bin = self._find_java_binary(home) if home is not None else "java"
                major_version, supports_compiler_modules = self._get_java_runtime_info(java_bin)
                if not supports_compiler_modules:
                    log.info("Skipping JavaLight runtime candidate %s: missing jdk.compiler", source)
                    continue
                if self._required_java_version is not None and (
                    major_version is None or major_version < self._required_java_version
                ):
                    log.warning(
                        "Skipping JavaLight runtime candidate %s: project requires JDK >= %s but candidate is %s",
                        source,
                        self._required_java_version,
                        major_version,
                    )
                    continue
                log.info(
                    "Selected JavaLight runtime candidate %s with JDK %s (project requires >= %s)",
                    source,
                    major_version,
                    self._required_java_version,
                )
                self._resolved_java_version = major_version
                return java_bin, home if isinstance(home, str) else None
            if self._required_java_version is not None:
                raise RuntimeError(
                    f"JavaLight requires JDK >= {self._required_java_version} for this project"
                )
            raise RuntimeError("JavaLight requires a JDK runtime with the jdk.compiler module")

        def _java_supports_compiler_modules(self, java_bin: str) -> bool:
            _, supports_compiler_modules = self._get_java_runtime_info(java_bin)
            return supports_compiler_modules

        def _get_java_runtime_info(self, java_bin: str) -> tuple[int | None, bool]:
            try:
                modules_result = subprocess.run(
                    [java_bin, "--list-modules"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                    env=self.env_copy(),
                )
            except (OSError, ValueError, subprocess.SubprocessError):
                return None, False
            if modules_result.returncode != 0:
                return None, False
            supports_compiler_modules = "jdk.compiler@" in (modules_result.stdout or "")
            try:
                version_result = subprocess.run(
                    [java_bin, "-version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                    env=self.env_copy(),
                )
            except (OSError, ValueError, subprocess.SubprocessError):
                return None, supports_compiler_modules
            version_text = "\n".join(
                part for part in (version_result.stdout or "", version_result.stderr or "") if part
            )
            return self._parse_java_major_version(version_text), supports_compiler_modules

        def _detect_required_java_version(self) -> int | None:
            root = pathlib.Path(self._repository_root_path)
            for path in (root / "pom.xml", root / "build.gradle", root / "build.gradle.kts", root / ".java-version"):
                if not path.exists():
                    continue
                contents = path.read_text(encoding="utf-8", errors="ignore")
                version = self._extract_java_version_from_contents(path.name, contents)
                if version is not None:
                    return version
            return None

        def _extract_java_version_from_contents(self, file_name: str, contents: str) -> int | None:
            patterns: list[str]
            if file_name == "pom.xml":
                patterns = [
                    r"<maven\.compiler\.release>\s*([^<\s]+)\s*</maven\.compiler\.release>",
                    r"<maven\.compiler\.source>\s*([^<\s]+)\s*</maven\.compiler\.source>",
                    r"<java\.version>\s*([^<\s]+)\s*</java\.version>",
                ]
            elif file_name == "build.gradle":
                patterns = [
                    r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)",
                    r"sourceCompatibility\s*=\s*['\"]?([0-9.]+)['\"]?",
                    r"targetCompatibility\s*=\s*['\"]?([0-9.]+)['\"]?",
                    r"jvmTarget\s*=\s*['\"]?([0-9.]+)['\"]?",
                ]
            elif file_name == "build.gradle.kts":
                patterns = [
                    r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)",
                    r"JavaVersion\.VERSION_(\d+)",
                    r"sourceCompatibility\s*=\s*JavaVersion\.VERSION_(\d+)",
                    r"targetCompatibility\s*=\s*JavaVersion\.VERSION_(\d+)",
                    r"jvmTarget\s*=\s*['\"]?([0-9.]+)['\"]?",
                ]
            elif file_name == ".java-version":
                return self._normalize_java_version_token(contents.strip())
            else:
                return None

            for pattern in patterns:
                match = re.search(pattern, contents)
                if match is None:
                    continue
                version = self._normalize_java_version_token(match.group(1))
                if version is not None:
                    return version
            return None

        def _normalize_java_version_token(self, raw: str) -> int | None:
            token = raw.strip().strip('"').strip("'")
            if token.startswith("1."):
                token = token[2:]
            match = re.match(r"^(\d+)", token)
            if match is None:
                return None
            try:
                return int(match.group(1))
            except ValueError:
                return None

        def _parse_java_major_version(self, text: str) -> int | None:
            match = re.search(r'version\s+"([^"]+)"', text)
            if match is None:
                return None
            return self._normalize_java_version_token(match.group(1))

        def _bootstrap_managed_server_home(self, target_home: str) -> None:
            bootstrap_home = os.environ.get("SARI_JAVALIGHT_BOOTSTRAP_HOME")
            if not bootstrap_home:
                potential_path = os.path.abspath(os.path.join(os.getcwd(), "tools", "manual", "java-language-server"))
                if os.path.exists(potential_path):
                    bootstrap_home = potential_path
            if not bootstrap_home:
                bundled_home = self._bundled_server_home()
                if self._has_server_classpath(bundled_home):
                    bootstrap_home = bundled_home

            if not bootstrap_home or not self._has_server_classpath(bootstrap_home):
                raise RuntimeError(
                    "JavaLight bootstrap source not found. Set SARI_JAVALIGHT_BOOTSTRAP_HOME or build "
                    "tools/manual/java-language-server first."
                )

            if os.path.exists(target_home):
                shutil.rmtree(target_home)
            shutil.copytree(bootstrap_home, target_home)

        def _bundled_server_home(self) -> str:
            return str(pathlib.Path(__file__).resolve().parent / "_vendor" / "javalight")

        def _has_server_classpath(self, server_home: str) -> bool:
            return os.path.exists(os.path.join(server_home, "dist", "classpath"))

        def _find_java_binary(self, base_path: str) -> str:
            if base_path is None:
                return "java"
            if not os.path.exists(base_path):
                return "java"
            # Common patterns for JRE installations
            search_patterns = ["bin/java", "bin/java.exe", "Contents/Home/bin/java"]
            # Also handle the nested folder often created by tar extraction
            # e.g., java21/jdk-21.0.2+13-jre/bin/java
            entries = sorted(
                pathlib.Path(base_path).iterdir(),
                key=lambda entry: (
                    1 if "-jre" in entry.name.lower() else 0,
                    entry.name,
                ),
            )
            for entry in entries:
                if entry.is_dir():
                    for pattern in search_patterns:
                        full_path = entry / pattern
                        if full_path.exists():
                            return str(full_path)
            
            for pattern in search_patterns:
                full_path = os.path.join(base_path, pattern)
                if os.path.exists(full_path):
                    return full_path
            return "java"

    @override
    def request_references(self, relative_file_path: str, line: int, column: int) -> list[Location]:
        abs_path = os.path.join(self.repository_root_path, relative_file_path)
        file_uri = pathlib.Path(abs_path).as_uri()

        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                self.server.notify.did_open_text_document({
                    "textDocument": {"uri": file_uri, "languageId": "java", "version": 1, "text": content}
                })
            except Exception as e:
                log.debug(f"Auto-open failed: {e}")

        raw_refs: list[dict] = self.server.send._send_request("textDocument/references", {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": column},
            "context": {"includeDeclaration": False}
        })

        if not raw_refs: return []
        return [cast(Location, ref) for ref in raw_refs if self._is_valid_reference(ref)]

    def _is_valid_reference(self, ref: dict) -> bool:
        uri = ref.get("uri", "")
        if not uri.startswith("file://"): return True
        path = uri.replace("file://", "")
        line_idx = ref.get("range", {}).get("start", {}).get("line", -1)
        if not os.path.exists(path) or line_idx < 0: return True
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    if i == line_idx:
                        s = line.strip()
                        if s.startswith("import "): return False
                        if any(s.startswith(p) for p in ("//", "/*", "*")): return False
                        return True
        except: return True
        return True

    @staticmethod
    def _get_initialize_params(repository_absolute_path: str) -> InitializeParams:
        return cast(InitializeParams, {
            "processId": os.getpid(),
            "rootPath": repository_absolute_path,
            "rootUri": pathlib.Path(repository_absolute_path).as_uri(),
            "capabilities": {
                "workspace": {"symbol": {"dynamicRegistration": True}, "workspaceFolders": True, "configuration": True},
                "textDocument": {
                    "synchronization": {"didSave": True},
                    "definition": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True}
                }
            }
        })

    def _start_server(self) -> None:
        self.server.on_request("client/registerCapability", lambda params: None)
        def handle_config(params):
            items = params.get("items", [])
            return [{} for _ in items]
        self.server.on_request("workspace/configuration", handle_config)
        def handle_folders(params):
            return [{"uri": pathlib.Path(self.repository_root_path).as_uri(), "name": os.path.basename(self.repository_root_path)}]
        self.server.on_request("workspace/workspaceFolders", handle_folders)
        self.server.start()
        self.server.send.initialize(self._get_initialize_params(self.repository_root_path))
        self.server.notify.initialized({})
