"""Java LSP provider benchmark helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import os
import re
import statistics
import time
from typing import Callable, Literal

from solidlsp.language_servers.java_light_server import JavaLightServer
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.settings import SolidLSPSettings


JavaProvider = Literal["javalight"]
JavaRequestKind = Literal["startup", "documentSymbol", "definition", "references", "workspaceSymbol"]
BenchmarkPhase = Literal["cold", "warm"]


@dataclass(frozen=True)
class SymbolRequest:
    relative_path: str
    line: int
    column: int


@dataclass(frozen=True)
class JavaBenchmarkTargets:
    document_symbol_paths: list[str]
    definition_request: SymbolRequest
    reference_request: SymbolRequest
    workspace_query: str


@dataclass(frozen=True)
class BenchmarkObservation:
    provider: JavaProvider
    request_kind: JavaRequestKind
    phase: BenchmarkPhase
    case_name: str
    latency_ms: float
    success: bool
    result_count: int
    error: str | None = None
    paths: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    selected_java_home: str | None = None
    selected_java_bin: str | None = None
    selected_java_version: int | None = None
    required_java_version: int | None = None


@dataclass(frozen=True)
class BenchmarkAggregate:
    provider: JavaProvider
    request_kind: JavaRequestKind
    phase: BenchmarkPhase
    run_count: int
    success_rate: float
    median_latency_ms: float
    median_result_count: int


def _iter_java_files(repo_root: str) -> list[Path]:
    root = Path(repo_root)
    return sorted(root.rglob("*.java"))


def _relative_path(repo_root: str, path: Path) -> str:
    return str(path.relative_to(repo_root))


def _find_first_line_and_column(contents: str, pattern: str) -> tuple[int, int]:
    for line_no, line in enumerate(contents.splitlines()):
        col = line.find(pattern)
        if col >= 0:
            return line_no, col
    raise ValueError(f"pattern not found: {pattern}")


def _find_word_position(contents: str, word: str) -> tuple[int, int]:
    compiled = re.compile(rf"\b{re.escape(word)}\b")
    for line_no, line in enumerate(contents.splitlines()):
        match = compiled.search(line)
        if match is not None:
            return line_no, match.start()
    raise ValueError(f"word not found: {word}")


def _pick_application_file(repo_root: str) -> Path | None:
    for path in _iter_java_files(repo_root):
        if path.name.endswith("Application.java"):
            return path
    return None


def discover_java_benchmark_targets(repo_root: str) -> JavaBenchmarkTargets:
    root = Path(repo_root)
    if not any((root / marker).exists() for marker in ("pom.xml", "build.gradle", "build.gradle.kts")):
        raise RuntimeError(f"repo does not look like a Java build project: {repo_root}")

    java_files = _iter_java_files(repo_root)
    service_files = [path for path in java_files if "/service/" in path.as_posix()]
    controller_files = [path for path in java_files if "/controller/" in path.as_posix()]
    if len(service_files) == 0 or len(controller_files) == 0:
        raise RuntimeError("could not find both service and controller Java files for benchmark discovery")

    chosen_service: Path | None = None
    chosen_controller: Path | None = None
    service_name = ""
    for service_path in service_files:
        service_name = service_path.stem
        for controller_path in controller_files:
            contents = controller_path.read_text(encoding="utf-8", errors="ignore")
            if re.search(rf"\b{re.escape(service_name)}\b", contents) is not None:
                chosen_service = service_path
                chosen_controller = controller_path
                break
        if chosen_service is not None:
            break
    if chosen_service is None or chosen_controller is None:
        raise RuntimeError("could not find a controller that references a service class")

    service_contents = chosen_service.read_text(encoding="utf-8", errors="ignore")
    controller_contents = chosen_controller.read_text(encoding="utf-8", errors="ignore")
    service_decl_line, service_decl_col = _find_word_position(service_contents, chosen_service.stem)
    controller_use_line, controller_use_col = _find_word_position(controller_contents, chosen_service.stem)

    app_path = _pick_application_file(repo_root)
    document_symbol_paths = [
        _relative_path(repo_root, chosen_controller),
        _relative_path(repo_root, chosen_service),
    ]
    if app_path is not None:
        document_symbol_paths.append(_relative_path(repo_root, app_path))

    return JavaBenchmarkTargets(
        document_symbol_paths=document_symbol_paths,
        definition_request=SymbolRequest(
            relative_path=_relative_path(repo_root, chosen_controller),
            line=controller_use_line,
            column=controller_use_col,
        ),
        reference_request=SymbolRequest(
            relative_path=_relative_path(repo_root, chosen_service),
            line=service_decl_line,
            column=service_decl_col,
        ),
        workspace_query=chosen_service.stem,
    )


def summarize_observations(observations: list[BenchmarkObservation]) -> list[BenchmarkAggregate]:
    grouped: dict[tuple[str, str, str], list[BenchmarkObservation]] = {}
    for item in observations:
        key = (item.provider, item.request_kind, item.phase)
        grouped.setdefault(key, []).append(item)

    aggregates: list[BenchmarkAggregate] = []
    for key, items in sorted(grouped.items()):
        latency_values = [item.latency_ms for item in items]
        result_counts = [item.result_count for item in items]
        success_count = len([item for item in items if item.success])
        aggregates.append(
            BenchmarkAggregate(
                provider=key[0],  # type: ignore[arg-type]
                request_kind=key[1],  # type: ignore[arg-type]
                phase=key[2],  # type: ignore[arg-type]
                run_count=len(items),
                success_rate=(success_count / len(items)) if len(items) > 0 else 0.0,
                median_latency_ms=float(statistics.median(latency_values)),
                median_result_count=int(statistics.median(result_counts)),
            )
        )
    return aggregates


def build_markdown_report(*, repo_root: str, observations: list[BenchmarkObservation]) -> str:
    provider_runtime: dict[str, BenchmarkObservation] = {}
    for item in observations:
        if item.request_kind == "startup" and item.success and item.provider not in provider_runtime:
            provider_runtime[item.provider] = item
    lines = [
        "# Java LSP Benchmark Report",
        "",
        f"- Repo: `{repo_root}`",
        f"- Total observations: `{len(observations)}`",
        "",
    ]
    if provider_runtime:
        lines.extend(
            [
                "## Runtime Selection",
                "",
                "| Provider | Required Java | Selected Java | JAVA_HOME | Java Bin |",
                "| --- | ---: | ---: | --- | --- |",
            ]
        )
        for provider, item in sorted(provider_runtime.items()):
            lines.append(
                f"| {provider} | {item.required_java_version or '-'} | {item.selected_java_version or '-'} | "
                f"{item.selected_java_home or '-'} | {item.selected_java_bin or '-'} |"
            )
        lines.append("")
    lines.extend(
        [
        "| Provider | Request | Phase | Runs | Success Rate | Median Latency (ms) | Median Result Count |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in summarize_observations(observations):
        lines.append(
            f"| {item.provider} | {item.request_kind} | {item.phase} | {item.run_count} | "
            f"{item.success_rate:.2f} | {item.median_latency_ms:.1f} | {item.median_result_count} |"
        )
    return "\n".join(lines) + "\n"


def _with_provider_env(provider: JavaProvider, *, base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    return env


def _provider_class(provider: JavaProvider):
    return JavaLightServer


def _measure_call(fn: Callable[[], object]) -> tuple[float, object]:
    started = time.perf_counter()
    result = fn()
    return (time.perf_counter() - started) * 1000.0, result


def _result_count(result: object) -> int:
    if result is None:
        return 0
    if isinstance(result, list):
        return len(result)
    return 1


def _extract_paths(result: object) -> tuple[str, ...]:
    if not isinstance(result, list):
        return ()
    paths: list[str] = []
    for item in result:
        if isinstance(item, dict):
            value = item.get("relativePath")
        else:
            value = getattr(item, "relativePath", None)
        if isinstance(value, str) and value.strip() != "":
            paths.append(value)
    return tuple(paths)


def _extract_names(result: object) -> tuple[str, ...]:
    if not isinstance(result, list):
        return ()
    names: list[str] = []
    for item in result:
        if isinstance(item, dict):
            value = item.get("name")
        else:
            value = getattr(item, "name", None)
        if isinstance(value, str) and value.strip() != "":
            names.append(value)
    return tuple(names)


def _extract_runtime_metadata(server: object) -> dict[str, object | None]:
    provider = getattr(server, "_dependency_provider", None)
    if provider is None:
        return {}
    return {
        "selected_java_home": getattr(provider, "_resolved_java_home", None),
        "selected_java_bin": getattr(provider, "_resolved_java_bin", None),
        "selected_java_version": getattr(provider, "_resolved_java_version", None),
        "required_java_version": getattr(provider, "_required_java_version", None),
    }


def run_java_lsp_benchmark(
    *,
    repo_root: str,
    providers: tuple[JavaProvider, ...] = ("javalight",),
    repeats: int = 3,
    javalight_home: str | None = None,
) -> list[BenchmarkObservation]:
    targets = discover_java_benchmark_targets(repo_root)
    observations: list[BenchmarkObservation] = []

    for provider in providers:
        server_class = _provider_class(provider)
        for _ in range(repeats):
            original_env = os.environ.copy()
            os.environ.clear()
            os.environ.update(_with_provider_env(provider, base_env=original_env))
            if javalight_home is not None and provider == "javalight":
                os.environ["SARI_JAVALIGHT_HOME"] = javalight_home
            try:
                settings = SolidLSPSettings()
                config = LanguageServerConfig(code_language=Language.JAVA)

                startup_server = None
                try:
                    startup_server = server_class(config, repo_root, settings)
                    latency_ms, _ = _measure_call(startup_server.start)
                    runtime_meta = _extract_runtime_metadata(startup_server)
                    observations.append(
                        BenchmarkObservation(
                            provider=provider,
                            request_kind="startup",
                            phase="cold",
                            case_name="startup",
                            latency_ms=latency_ms,
                            success=True,
                            result_count=1,
                            selected_java_home=runtime_meta.get("selected_java_home"),  # type: ignore[arg-type]
                            selected_java_bin=runtime_meta.get("selected_java_bin"),  # type: ignore[arg-type]
                            selected_java_version=runtime_meta.get("selected_java_version"),  # type: ignore[arg-type]
                            required_java_version=runtime_meta.get("required_java_version"),  # type: ignore[arg-type]
                        )
                    )
                except (RuntimeError, OSError, TimeoutError, ValueError) as exc:
                    observations.append(
                        BenchmarkObservation(
                            provider=provider,
                            request_kind="startup",
                            phase="cold",
                            case_name="startup",
                            latency_ms=0.0,
                            success=False,
                            result_count=0,
                            error=str(exc),
                        )
                    )
                    continue
                finally:
                    if startup_server is not None:
                        startup_server.stop()

                request_server = server_class(config, repo_root, settings).start()
                try:
                    for path in targets.document_symbol_paths:
                        latency_ms, result = _measure_call(lambda path=path: request_server.request_document_symbols(path))
                        observations.append(
                            BenchmarkObservation(
                                provider=provider,
                                request_kind="documentSymbol",
                                phase="cold",
                                case_name=path,
                                latency_ms=latency_ms,
                                success=True,
                                result_count=_result_count(result),
                                paths=_extract_paths(result),
                                names=_extract_names(result),
                            )
                        )
                        warm_latency_ms, warm_result = _measure_call(lambda path=path: request_server.request_document_symbols(path))
                        observations.append(
                            BenchmarkObservation(
                                provider=provider,
                                request_kind="documentSymbol",
                                phase="warm",
                                case_name=path,
                                latency_ms=warm_latency_ms,
                                success=True,
                                result_count=_result_count(warm_result),
                                paths=_extract_paths(warm_result),
                                names=_extract_names(warm_result),
                            )
                        )

                    latency_ms, result = _measure_call(
                        lambda: request_server.request_definition(
                            targets.definition_request.relative_path,
                            targets.definition_request.line,
                            targets.definition_request.column,
                        )
                    )
                    observations.append(
                        BenchmarkObservation(
                            provider=provider,
                            request_kind="definition",
                            phase="cold",
                            case_name=targets.definition_request.relative_path,
                            latency_ms=latency_ms,
                            success=True,
                            result_count=_result_count(result),
                            paths=_extract_paths(result),
                            names=_extract_names(result),
                        )
                    )

                    latency_ms, result = _measure_call(
                        lambda: request_server.request_references(
                            targets.reference_request.relative_path,
                            targets.reference_request.line,
                            targets.reference_request.column,
                        )
                    )
                    observations.append(
                        BenchmarkObservation(
                            provider=provider,
                            request_kind="references",
                            phase="cold",
                            case_name=targets.reference_request.relative_path,
                            latency_ms=latency_ms,
                            success=True,
                            result_count=_result_count(result),
                            paths=_extract_paths(result),
                            names=_extract_names(result),
                        )
                    )
                    warm_latency_ms, warm_result = _measure_call(
                        lambda: request_server.request_references(
                            targets.reference_request.relative_path,
                            targets.reference_request.line,
                            targets.reference_request.column,
                        )
                    )
                    observations.append(
                        BenchmarkObservation(
                            provider=provider,
                            request_kind="references",
                            phase="warm",
                            case_name=targets.reference_request.relative_path,
                            latency_ms=warm_latency_ms,
                            success=True,
                            result_count=_result_count(warm_result),
                            paths=_extract_paths(warm_result),
                            names=_extract_names(warm_result),
                        )
                    )

                    latency_ms, result = _measure_call(lambda: request_server.request_workspace_symbol(targets.workspace_query))
                    observations.append(
                        BenchmarkObservation(
                            provider=provider,
                            request_kind="workspaceSymbol",
                            phase="cold",
                            case_name=targets.workspace_query,
                            latency_ms=latency_ms,
                            success=True,
                            result_count=_result_count(result),
                            paths=_extract_paths(result),
                            names=_extract_names(result),
                        )
                    )
                except (RuntimeError, OSError, TimeoutError, ValueError) as exc:
                    observations.append(
                        BenchmarkObservation(
                            provider=provider,
                            request_kind="references",
                            phase="cold",
                            case_name="benchmark_run",
                            latency_ms=0.0,
                            success=False,
                            result_count=0,
                            error=str(exc),
                        )
                    )
                finally:
                    request_server.stop()
            finally:
                os.environ.clear()
                os.environ.update(original_env)

    return observations


def observations_to_jsonable(observations: list[BenchmarkObservation]) -> list[dict[str, object]]:
    return [asdict(item) for item in observations]
