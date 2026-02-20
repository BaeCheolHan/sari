"""LSP 런타임 선택/환경 주입 정책을 캡슐화한다."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess

from solidlsp.ls_config import Language


@dataclass(frozen=True)
class RuntimeRequirementDTO:
    """런타임 요구사항 DTO."""

    language: Language
    runtime_name: str
    minimum_major: int


@dataclass(frozen=True)
class RuntimeLaunchContextDTO:
    """런타임 해석 결과 DTO."""

    requirement: RuntimeRequirementDTO | None
    env_overrides: dict[str, str]
    selected_executable: str | None
    selected_major: int | None
    selected_source: str | None
    auto_provision_expected: bool


class LspRuntimeBroker:
    """언어별 런타임 선택/환경 주입을 담당한다."""

    _JAVA_REQUIRED_LANGUAGES = {Language.JAVA, Language.KOTLIN, Language.GROOVY, Language.SCALA}

    def __init__(self, java_min_major: int = 17) -> None:
        self._java_min_major = max(8, int(java_min_major))

    def resolve(self, language: Language) -> RuntimeLaunchContextDTO:
        """지정 언어에 대한 런타임/환경 주입 결과를 반환한다."""
        env_overrides = self._tool_path_overrides(language)
        if language not in self._JAVA_REQUIRED_LANGUAGES:
            return RuntimeLaunchContextDTO(
                requirement=None,
                env_overrides=env_overrides,
                selected_executable=None,
                selected_major=None,
                selected_source=None,
                auto_provision_expected=False,
            )

        requirement = RuntimeRequirementDTO(language=language, runtime_name="java", minimum_major=self._java_min_major)
        selected = self._select_compatible_java()
        if selected is None:
            # Java 계열 LS는 solidlsp 내부 auto-provision 경로가 있으므로 시작 자체를 막지 않는다.
            return RuntimeLaunchContextDTO(
                requirement=requirement,
                env_overrides=env_overrides,
                selected_executable=None,
                selected_major=None,
                selected_source=None,
                auto_provision_expected=True,
            )

        selected_path, selected_major, selected_source = selected
        java_home = selected_path.parent.parent
        merged_path = self._prepend_to_path(str(java_home / "bin"))
        resolved_overrides = dict(env_overrides)
        resolved_overrides["JAVA_HOME"] = str(java_home)
        resolved_overrides["PATH"] = merged_path
        return RuntimeLaunchContextDTO(
            requirement=requirement,
            env_overrides=resolved_overrides,
            selected_executable=str(selected_path),
            selected_major=selected_major,
            selected_source=selected_source,
            auto_provision_expected=False,
        )

    def _select_compatible_java(self) -> tuple[Path, int, str] | None:
        candidates = self._candidate_java_executables()
        best: tuple[Path, int, str] | None = None
        for source, executable in candidates:
            major = self._probe_java_major(executable)
            if major is None or major < self._java_min_major:
                continue
            if best is None or major > best[1]:
                best = (executable, major, source)
        return best

    def _candidate_java_executables(self) -> list[tuple[str, Path]]:
        candidates: list[tuple[str, Path]] = []
        override_bin = os.environ.get("SARI_LSP_JAVA_BIN", "").strip()
        if override_bin != "":
            override_path = Path(override_bin).expanduser()
            if override_path.exists():
                candidates.append(("env:SARI_LSP_JAVA_BIN", override_path))
        java_home = os.environ.get("JAVA_HOME", "").strip()
        if java_home != "":
            from_java_home = Path(java_home).expanduser() / "bin" / "java"
            if from_java_home.exists():
                candidates.append(("env:JAVA_HOME", from_java_home))
        in_path = shutil.which("java")
        if in_path is not None and in_path.strip() != "":
            candidates.append(("path:java", Path(in_path)))
        for cached in self._discover_cached_java_executables():
            candidates.append(("cache:solidlsp", cached))
        deduped: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for source, path in candidates:
            normalized = str(path.resolve()) if path.exists() else str(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append((source, path))
        return deduped

    def _discover_cached_java_executables(self) -> list[Path]:
        root = Path.home() / ".solidlsp" / "language_servers" / "static"
        if not root.exists():
            return []
        results: list[Path] = []
        for candidate in root.rglob("java"):
            if not candidate.is_file():
                continue
            if candidate.parent.name != "bin":
                continue
            results.append(candidate)
        return results

    def _probe_java_major(self, java_executable: Path) -> int | None:
        try:
            result = subprocess.run(
                [str(java_executable), "-version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return self.parse_java_major_version(f"{result.stderr}\n{result.stdout}")

    @staticmethod
    def parse_java_major_version(version_text: str) -> int | None:
        """`java -version` 출력에서 major 버전을 파싱한다."""
        match = re.search(r'version\s+"([^"]+)"', version_text)
        if match is None:
            return None
        raw_version = match.group(1).strip()
        if raw_version.startswith("1."):
            parts = raw_version.split(".")
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
            return None
        major_text = raw_version.split(".")[0]
        if major_text.isdigit():
            return int(major_text)
        return None

    def _tool_path_overrides(self, language: Language) -> dict[str, str]:
        extra_paths: list[str] = []
        overrides: dict[str, str] = {}
        home = str(Path.home())
        if language == Language.GO:
            extra_paths.extend(self._discover_go_bin_paths(home=home))
        if language == Language.RUBY:
            extra_paths.extend([f"{home}/.gem/ruby/2.6.0/bin", "/opt/homebrew/lib/ruby/gems/4.0.0/bin", "/opt/homebrew/opt/ruby/bin"])
        if language == Language.PERL:
            extra_paths.append(f"{home}/perl5/bin")
            current_perl5 = os.environ.get("PERL5LIB", "").strip()
            perl_lib = f"{home}/perl5/lib/perl5"
            if current_perl5 == "":
                overrides["PERL5LIB"] = perl_lib
            elif perl_lib not in current_perl5.split(":"):
                overrides["PERL5LIB"] = f"{perl_lib}:{current_perl5}"
        existing = [path_item for path_item in extra_paths if Path(path_item).exists()]
        if len(existing) > 0:
            overrides["PATH"] = self._prepend_to_path(*existing)
        return overrides

    def _discover_go_bin_paths(self, home: str) -> list[str]:
        """gopls 탐색 성공률을 높이기 위해 Go bin 후보 경로를 수집한다."""
        candidates: list[str] = []
        gopath_env = os.environ.get("GOPATH", "").strip()
        if gopath_env != "":
            for root in gopath_env.split(os.pathsep):
                normalized = root.strip()
                if normalized != "":
                    candidates.append(str(Path(normalized).expanduser() / "bin"))
        try:
            result = subprocess.run(
                ["go", "env", "GOPATH"],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.5,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            for root in result.stdout.strip().split(os.pathsep):
                normalized = root.strip()
                if normalized != "":
                    candidates.append(str(Path(normalized).expanduser() / "bin"))
        candidates.append(f"{home}/go/bin")
        deduped: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            expanded = str(Path(item).expanduser())
            if expanded in seen:
                continue
            seen.add(expanded)
            deduped.append(expanded)
        return deduped

    @staticmethod
    def _prepend_to_path(*new_paths: str) -> str:
        current = os.environ.get("PATH", "")
        parts = [part for part in current.split(":") if part != ""]
        for path_item in reversed([path for path in new_paths if path != ""]):
            if path_item not in parts:
                parts.insert(0, path_item)
        return ":".join(parts)
