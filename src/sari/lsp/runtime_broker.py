"""LSP 런타임 선택/환경 주입 정책을 캡슐화한다."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any

from solidlsp.ls_config import Language

log = logging.getLogger(__name__)


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
    _REPO_JAVA_SIGNAL_FILES = (
        ".java-version",
        ".sdkmanrc",
        "gradle.properties",
        "build.gradle",
        "build.gradle.kts",
        "pom.xml",
    )
    _MAX_PARSE_BYTES = 2 * 1024 * 1024
    _CACHE_VERSION = 1

    def __init__(self, java_min_major: int = 17) -> None:
        self._java_min_major = max(8, int(java_min_major))
        self._cache_lock = threading.Lock()
        self._cached_static_java_bins: list[Path] = []
        self._cached_static_java_bins_at_monotonic = 0.0
        self._cached_static_java_bins_ttl_sec = max(1.0, self._parse_cache_ttl_sec())
        self._java_major_probe_cache: dict[str, tuple[float, int, int | None]] = {}
        self._repo_required_major_cache: dict[str, tuple[str, int | None]] = {}

        self._runtime_cache_enabled = self._env_enabled("SARI_LSP_JAVA_RUNTIME_CACHE_ENABLED", default=True)
        self._bundled_fallback_enabled = self._env_enabled("SARI_LSP_BUNDLED_JRE_DOWNLOAD_ENABLED", default=True)
        self._repo_cache_path = self._resolve_path_env(
            "SARI_LSP_JAVA_RUNTIME_REPO_CACHE_PATH",
            Path.home() / ".local" / "share" / "sari-v2" / "java_runtime_repo_cache.json",
        )
        self._inventory_cache_path = self._resolve_path_env(
            "SARI_LSP_JAVA_RUNTIME_INVENTORY_CACHE_PATH",
            Path.home() / ".local" / "share" / "sari-v2" / "java_runtime_inventory.json",
        )
        self._repo_cache_loaded = False
        self._inventory_cache_loaded = False
        self._repo_cache_data: dict[str, Any] = {"version": self._CACHE_VERSION, "repos": {}}
        self._inventory_cache_data: dict[str, Any] = {"version": self._CACHE_VERSION, "entries": {}}

    def resolve(self, language: Language, repo_root: str | None = None) -> RuntimeLaunchContextDTO:
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

        normalized_repo_root = self._normalize_repo_root(repo_root)
        repo_required_major = self._resolve_repo_required_major(normalized_repo_root)
        effective_required = max(self._java_min_major, repo_required_major or self._java_min_major)
        requirement = RuntimeRequirementDTO(language=language, runtime_name="java", minimum_major=effective_required)

        if self._runtime_cache_enabled:
            cached = self._resolve_from_repo_cache(normalized_repo_root, effective_required)
            if cached is not None:
                selected_path, selected_major, _source = cached
                return self._build_context(
                    requirement=requirement,
                    env_overrides=env_overrides,
                    selected_path=selected_path,
                    selected_major=selected_major,
                    selected_source="persist:repo_java_runtime_cache",
                )

        selected = self._select_compatible_java(min_major=effective_required)
        if selected is None and self._bundled_fallback_enabled:
            selected = self._select_bundled_java_fallback(min_major=effective_required)
        if selected is None:
            selected = self._select_compatible_java(min_major=1)
            if selected is not None:
                log.warning(
                    "Java runtime mismatch(permissive): selected_major=%s effective_required=%s repo=%s",
                    selected[1],
                    effective_required,
                    normalized_repo_root,
                )

        if selected is None:
            return RuntimeLaunchContextDTO(
                requirement=requirement,
                env_overrides=env_overrides,
                selected_executable=None,
                selected_major=None,
                selected_source=None,
                auto_provision_expected=True,
            )

        selected_path, selected_major, selected_source = selected
        if self._runtime_cache_enabled:
            self._save_repo_cache_selection(
                normalized_repo_root=normalized_repo_root,
                required_major=effective_required,
                selected_path=selected_path,
                selected_major=selected_major,
                selected_source=selected_source,
            )
        return self._build_context(
            requirement=requirement,
            env_overrides=env_overrides,
            selected_path=selected_path,
            selected_major=selected_major,
            selected_source=selected_source,
        )

    def _build_context(
        self,
        *,
        requirement: RuntimeRequirementDTO,
        env_overrides: dict[str, str],
        selected_path: Path,
        selected_major: int,
        selected_source: str,
    ) -> RuntimeLaunchContextDTO:
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

    def _normalize_repo_root(self, repo_root: str | None) -> str | None:
        if repo_root is None:
            return None
        stripped = repo_root.strip()
        if stripped == "":
            return None
        return str(Path(stripped).expanduser().resolve())

    def _resolve_repo_required_major(self, normalized_repo_root: str | None) -> int | None:
        if normalized_repo_root is None:
            return None
        fingerprint = self._compute_repo_fingerprint(normalized_repo_root)
        cached = self._repo_required_major_cache.get(normalized_repo_root)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        parsed = self._parse_repo_required_major(normalized_repo_root)
        self._repo_required_major_cache[normalized_repo_root] = (fingerprint, parsed)
        return parsed

    def _compute_repo_fingerprint(self, normalized_repo_root: str) -> str:
        hasher = hashlib.sha1(normalized_repo_root.encode("utf-8"), usedforsecurity=False)
        root = Path(normalized_repo_root)
        for rel in self._REPO_JAVA_SIGNAL_FILES:
            target = root / rel
            if not target.exists() or not target.is_file():
                continue
            try:
                stat_result = target.stat()
            except OSError:
                continue
            hasher.update(rel.encode("utf-8"))
            hasher.update(str(float(stat_result.st_mtime)).encode("utf-8"))
            hasher.update(str(int(stat_result.st_size)).encode("utf-8"))
        return hasher.hexdigest()

    def _parse_repo_required_major(self, normalized_repo_root: str) -> int | None:
        root = Path(normalized_repo_root)
        parser_order = (
            self._parse_java_version_file,
            self._parse_sdkmanrc,
            self._parse_gradle_properties,
            self._parse_gradle_build_scripts,
            self._parse_pom_xml,
        )
        for parser in parser_order:
            major = parser(root)
            if major is not None:
                return major
        return None

    def _parse_java_version_file(self, root: Path) -> int | None:
        target = root / ".java-version"
        text = self._read_small_text(target)
        if text is None:
            return None
        first = text.strip().splitlines()
        if len(first) == 0:
            return None
        return self._parse_major_token(first[0])

    def _parse_sdkmanrc(self, root: Path) -> int | None:
        target = root / ".sdkmanrc"
        text = self._read_small_text(target)
        if text is None:
            return None
        match = re.search(r"^\s*java\s*=\s*(.+)$", text, re.MULTILINE)
        if match is None:
            return None
        return self._parse_major_token(match.group(1))

    def _parse_gradle_properties(self, root: Path) -> int | None:
        target = root / "gradle.properties"
        text = self._read_small_text(target)
        if text is None:
            return None
        patterns = (
            r"^\s*java\.version\s*=\s*(.+)$",
            r"^\s*javaVersion\s*=\s*(.+)$",
            r"^\s*maven\.compiler\.release\s*=\s*(.+)$",
            r"^\s*maven\.compiler\.source\s*=\s*(.+)$",
            r"^\s*maven\.compiler\.target\s*=\s*(.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.MULTILINE)
            if match is None:
                continue
            parsed = self._parse_major_token(match.group(1))
            if parsed is not None:
                return parsed
        return None

    def _parse_gradle_build_scripts(self, root: Path) -> int | None:
        for name in ("build.gradle.kts", "build.gradle"):
            target = root / name
            text = self._read_small_text(target)
            if text is None:
                continue
            patterns = (
                r"JavaLanguageVersion\.of\((\d+)\)",
                r"sourceCompatibility\s*=\s*JavaVersion\.VERSION_(\d+)",
                r"targetCompatibility\s*=\s*JavaVersion\.VERSION_(\d+)",
                r"sourceCompatibility\s*=\s*[\"']?(\d+)",
                r"targetCompatibility\s*=\s*[\"']?(\d+)",
            )
            for pattern in patterns:
                match = re.search(pattern, text)
                if match is None:
                    continue
                parsed = self._parse_major_token(match.group(1))
                if parsed is not None:
                    return parsed
        return None

    def _parse_pom_xml(self, root: Path) -> int | None:
        target = root / "pom.xml"
        text = self._read_small_text(target)
        if text is None:
            return None
        tags = (
            "maven.compiler.release",
            "maven.compiler.source",
            "maven.compiler.target",
            "java.version",
        )
        for tag in tags:
            match = re.search(rf"<{re.escape(tag)}>([^<]+)</{re.escape(tag)}>", text)
            if match is None:
                continue
            parsed = self._parse_major_token(match.group(1))
            if parsed is not None:
                return parsed
        return None

    def _read_small_text(self, path: Path) -> str | None:
        if not path.exists() or not path.is_file():
            return None
        try:
            stat_result = path.stat()
        except OSError:
            return None
        if int(stat_result.st_size) > self._MAX_PARSE_BYTES:
            log.warning("Java version parse skipped by size limit(path=%s size=%d)", path, int(stat_result.st_size))
            return None
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    def _parse_major_token(self, token: str) -> int | None:
        cleaned = token.strip().strip('"').strip("'")
        if cleaned == "":
            return None
        if cleaned.startswith("1."):
            parts = cleaned.split(".")
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
        digit_match = re.search(r"(\d+)", cleaned)
        if digit_match is None:
            return None
        return int(digit_match.group(1))

    def _has_explicit_java_override(self) -> bool:
        if os.environ.get("SARI_LSP_JAVA_BIN", "").strip() != "":
            return True
        if os.environ.get("JAVA_HOME", "").strip() != "":
            return True
        return False

    def _resolve_from_repo_cache(self, normalized_repo_root: str | None, required_major: int) -> tuple[Path, int, str] | None:
        if normalized_repo_root is None:
            return None
        with self._cache_lock:
            self._ensure_repo_cache_loaded_locked()
            repos = self._repo_cache_data.get("repos")
            if not isinstance(repos, dict):
                return None
            repo_entry = repos.get(normalized_repo_root)
            if not isinstance(repo_entry, dict):
                return None
            current_fingerprint = self._compute_repo_fingerprint(normalized_repo_root)
            cached_fingerprint = str(repo_entry.get("repo_fingerprint", ""))
            if cached_fingerprint != current_fingerprint:
                return None
            major_map = repo_entry.get("major_map")
            if not isinstance(major_map, dict):
                return None
            selected_entry = major_map.get(str(required_major))
            if not isinstance(selected_entry, dict):
                return None
            selected_executable = selected_entry.get("selected_executable")
            selected_major = selected_entry.get("selected_major")
            if not isinstance(selected_executable, str) or not isinstance(selected_major, int):
                return None
            candidate = Path(selected_executable)
            try:
                stat_result = candidate.stat()
            except OSError:
                return None
            java_stat = selected_entry.get("java_stat")
            if not isinstance(java_stat, dict):
                return None
            cached_mtime = float(java_stat.get("mtime", -1.0))
            cached_size = int(java_stat.get("size", -1))
            current_mtime = float(stat_result.st_mtime)
            if abs(current_mtime - cached_mtime) > 1e-6 or int(stat_result.st_size) != cached_size:
                return None
            if selected_major < required_major:
                return None
            return (candidate, selected_major, str(selected_entry.get("selected_source", "persist")))

    def _save_repo_cache_selection(
        self,
        *,
        normalized_repo_root: str | None,
        required_major: int,
        selected_path: Path,
        selected_major: int,
        selected_source: str,
    ) -> None:
        if normalized_repo_root is None:
            return
        try:
            stat_result = selected_path.stat()
        except OSError:
            return
        with self._cache_lock:
            self._ensure_repo_cache_loaded_locked()
            repos = self._repo_cache_data.setdefault("repos", {})
            if not isinstance(repos, dict):
                repos = {}
                self._repo_cache_data["repos"] = repos
            repo_entry = repos.get(normalized_repo_root)
            if not isinstance(repo_entry, dict):
                repo_entry = {}
                repos[normalized_repo_root] = repo_entry
            repo_entry["repo_fingerprint"] = self._compute_repo_fingerprint(normalized_repo_root)
            major_map = repo_entry.get("major_map")
            if not isinstance(major_map, dict):
                major_map = {}
                repo_entry["major_map"] = major_map
            major_map[str(required_major)] = {
                "selected_executable": str(selected_path),
                "selected_major": int(selected_major),
                "selected_source": selected_source,
                "java_stat": {
                    "mtime": float(stat_result.st_mtime),
                    "size": int(stat_result.st_size),
                },
                "updated_at_epoch_sec": int(time.time()),
            }
            self._save_repo_cache_locked()

    def _select_compatible_java(self, min_major: int) -> tuple[Path, int, str] | None:
        candidates = self._candidate_java_executables()
        return self._pick_best_candidate(candidates=candidates, min_major=min_major)

    def _select_bundled_java_fallback(self, min_major: int) -> tuple[Path, int, str] | None:
        candidates = self._candidate_bundled_java_executables()
        best = self._pick_best_candidate(candidates=candidates, min_major=min_major)
        if best is not None:
            return best
        return self._pick_best_candidate(candidates=candidates, min_major=1)

    def _pick_best_candidate(self, candidates: list[tuple[str, Path]], min_major: int) -> tuple[Path, int, str] | None:
        best: tuple[Path, int, str] | None = None
        for source, executable in candidates:
            major = self._probe_java_major(executable)
            if major is None or major < min_major:
                continue
            if best is None or major > best[1]:
                best = (executable, major, source)
        return best

    def _candidate_bundled_java_executables(self) -> list[tuple[str, Path]]:
        candidates: list[tuple[str, Path]] = []
        override_dir = os.environ.get("SARI_LSP_BUNDLED_JRE_DIR", "").strip()
        if override_dir != "":
            override_path = Path(override_dir).expanduser() / "bin" / "java"
            if override_path.exists():
                candidates.append(("bundled:override", override_path))
        root = Path.home() / ".solidlsp" / "language_servers" / "static"
        if root.exists():
            for candidate in root.rglob("java"):
                if not candidate.is_file():
                    continue
                if candidate.parent.name != "bin":
                    continue
                if "jre" not in str(candidate.parent.parent).lower():
                    continue
                candidates.append(("bundled:solidlsp", candidate))
        deduped: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for source, path in candidates:
            normalized = str(path.resolve()) if path.exists() else str(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append((source, path))
        return deduped

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
        now = time.monotonic()
        with self._cache_lock:
            if now - self._cached_static_java_bins_at_monotonic <= self._cached_static_java_bins_ttl_sec:
                return list(self._cached_static_java_bins)
        if not root.exists():
            resolved: list[Path] = []
        else:
            resolved = []
            for candidate in root.rglob("java"):
                if not candidate.is_file():
                    continue
                if candidate.parent.name != "bin":
                    continue
                resolved.append(candidate)
        with self._cache_lock:
            self._cached_static_java_bins = list(resolved)
            self._cached_static_java_bins_at_monotonic = now
        return resolved

    def _probe_java_major(self, java_executable: Path) -> int | None:
        cache_key = str(java_executable.expanduser())
        try:
            stat_result = java_executable.stat()
        except OSError:
            return None
        mtime = float(stat_result.st_mtime)
        size = int(stat_result.st_size)
        with self._cache_lock:
            cached = self._java_major_probe_cache.get(cache_key)
            if cached is not None:
                cached_mtime, cached_size, cached_major = cached
                if cached_mtime == mtime and cached_size == size:
                    return cached_major
            self._ensure_inventory_cache_loaded_locked()
            entries = self._inventory_cache_data.get("entries")
            if isinstance(entries, dict):
                entry = entries.get(cache_key)
                if isinstance(entry, dict):
                    cached_mtime = float(entry.get("mtime", -1.0))
                    cached_size = int(entry.get("size", -1))
                    cached_major = entry.get("major")
                    if cached_mtime == mtime and cached_size == size and isinstance(cached_major, int):
                        self._java_major_probe_cache[cache_key] = (mtime, size, cached_major)
                        return cached_major
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
        parsed = self.parse_java_major_version(f"{result.stderr}\n{result.stdout}")
        with self._cache_lock:
            self._java_major_probe_cache[cache_key] = (mtime, size, parsed)
            self._save_inventory_entry_locked(
                cache_key=cache_key,
                major=parsed,
                mtime=mtime,
                size=size,
            )
        return parsed

    def _ensure_repo_cache_loaded_locked(self) -> None:
        if self._repo_cache_loaded:
            return
        self._repo_cache_data = self._load_json_cache(path=self._repo_cache_path, root_key="repos")
        self._repo_cache_loaded = True

    def _ensure_inventory_cache_loaded_locked(self) -> None:
        if self._inventory_cache_loaded:
            return
        self._inventory_cache_data = self._load_json_cache(path=self._inventory_cache_path, root_key="entries")
        self._inventory_cache_loaded = True

    def _load_json_cache(self, *, path: Path, root_key: str) -> dict[str, Any]:
        if not self._runtime_cache_enabled:
            return {"version": self._CACHE_VERSION, root_key: {}}
        if not path.exists():
            return {"version": self._CACHE_VERSION, root_key: {}}
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                return {"version": self._CACHE_VERSION, root_key: {}}
            parsed.setdefault("version", self._CACHE_VERSION)
            if not isinstance(parsed.get(root_key), dict):
                parsed[root_key] = {}
            return parsed
        except (OSError, json.JSONDecodeError, ValueError):
            log.warning("Invalid runtime cache file ignored(path=%s)", path)
            return {"version": self._CACHE_VERSION, root_key: {}}

    def _save_repo_cache_locked(self) -> None:
        self._save_json_cache(path=self._repo_cache_path, payload=self._repo_cache_data)

    def _save_inventory_entry_locked(self, *, cache_key: str, major: int | None, mtime: float, size: int) -> None:
        self._ensure_inventory_cache_loaded_locked()
        entries = self._inventory_cache_data.setdefault("entries", {})
        if not isinstance(entries, dict):
            entries = {}
            self._inventory_cache_data["entries"] = entries
        entries[cache_key] = {
            "major": major,
            "mtime": mtime,
            "size": size,
            "updated_at_epoch_sec": int(time.time()),
        }
        self._save_json_cache(path=self._inventory_cache_path, payload=self._inventory_cache_data)

    def _save_json_cache(self, *, path: Path, payload: dict[str, Any]) -> None:
        if not self._runtime_cache_enabled:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except OSError:
            log.warning("Failed to save runtime cache(path=%s)", path, exc_info=True)

    @staticmethod
    def _env_enabled(name: str, *, default: bool) -> bool:
        raw = os.environ.get(name, "").strip().lower()
        if raw == "":
            return default
        return raw not in {"0", "false", "no", "off"}

    @staticmethod
    def _resolve_path_env(name: str, default: Path) -> Path:
        raw = os.environ.get(name, "").strip()
        if raw == "":
            return default
        return Path(raw).expanduser()

    @staticmethod
    def _parse_cache_ttl_sec() -> float:
        raw = os.environ.get("SARI_LSP_JAVA_DISCOVERY_TTL_SEC", "").strip()
        if raw == "":
            return 30.0
        try:
            return float(raw)
        except ValueError:
            return 30.0

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
