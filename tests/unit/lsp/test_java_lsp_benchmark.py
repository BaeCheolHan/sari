from __future__ import annotations

from pathlib import Path

from sari.services.language_probe.java_lsp_benchmark import (
    BenchmarkObservation,
    build_markdown_report,
    discover_java_benchmark_targets,
    summarize_observations,
)


def test_discover_java_benchmark_targets_finds_service_controller_pair(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    service_path = tmp_path / "src" / "main" / "java" / "com" / "example" / "service" / "BudgetService.java"
    controller_path = tmp_path / "src" / "main" / "java" / "com" / "example" / "controller" / "BudgetController.java"
    app_path = tmp_path / "src" / "main" / "java" / "com" / "example" / "PaymentServerApplication.java"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    controller_path.parent.mkdir(parents=True, exist_ok=True)
    app_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        """
package com.example.service;

public class BudgetService {
    public String getBudget() {
        return "ok";
    }
}
""".strip(),
        encoding="utf-8",
    )
    controller_path.write_text(
        """
package com.example.controller;

import com.example.service.BudgetService;

public class BudgetController {
    private final BudgetService budgetService;
}
""".strip(),
        encoding="utf-8",
    )
    app_path.write_text(
        """
package com.example;

public class PaymentServerApplication {
    public static void main(String[] args) {}
}
""".strip(),
        encoding="utf-8",
    )

    targets = discover_java_benchmark_targets(str(tmp_path))

    assert targets.workspace_query == "BudgetService"
    assert targets.reference_request.relative_path.endswith("BudgetService.java")
    assert targets.definition_request.relative_path.endswith("BudgetController.java")
    assert targets.document_symbol_paths == [
        "src/main/java/com/example/controller/BudgetController.java",
        "src/main/java/com/example/service/BudgetService.java",
        "src/main/java/com/example/PaymentServerApplication.java",
    ]


def test_discover_java_benchmark_targets_accepts_gradle_java_project(tmp_path: Path) -> None:
    (tmp_path / "build.gradle").write_text("sourceCompatibility = '17'\n", encoding="utf-8")
    service_path = tmp_path / "src" / "main" / "java" / "com" / "example" / "service" / "BudgetService.java"
    controller_path = tmp_path / "src" / "main" / "java" / "com" / "example" / "controller" / "BudgetController.java"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    controller_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("package com.example.service; public class BudgetService {}", encoding="utf-8")
    controller_path.write_text(
        "package com.example.controller; import com.example.service.BudgetService; public class BudgetController { BudgetService s; }",
        encoding="utf-8",
    )

    targets = discover_java_benchmark_targets(str(tmp_path))

    assert targets.workspace_query == "BudgetService"


def test_summarize_observations_uses_median_latency() -> None:
    observations = [
        BenchmarkObservation(provider="javalight", request_kind="references", phase="cold", case_name="budget", latency_ms=30.0, success=True, result_count=4),
        BenchmarkObservation(provider="javalight", request_kind="references", phase="cold", case_name="budget", latency_ms=10.0, success=True, result_count=4),
        BenchmarkObservation(provider="javalight", request_kind="references", phase="cold", case_name="budget", latency_ms=20.0, success=True, result_count=4),
    ]

    summary = summarize_observations(observations)

    aggregate = summary[0]
    assert aggregate.provider == "javalight"
    assert aggregate.request_kind == "references"
    assert aggregate.phase == "cold"
    assert aggregate.median_latency_ms == 20.0
    assert aggregate.success_rate == 1.0
    assert aggregate.median_result_count == 4


def test_build_markdown_report_includes_provider_table() -> None:
    observations = [
        BenchmarkObservation(provider="javalight", request_kind="startup", phase="cold", case_name="startup", latency_ms=40.0, success=True, result_count=1, selected_java_home="/jdk21", selected_java_bin="/jdk21/bin/java", selected_java_version=21, required_java_version=17),
    ]

    report = build_markdown_report(repo_root="/tmp/payment-service", observations=observations)

    assert "# Java LSP Benchmark Report" in report
    assert "payment-service" in report
    assert "javalight" in report
    assert "Runtime Selection" in report
    assert "/jdk21/bin/java" in report
    assert "| Provider | Request | Phase |" in report


def test_run_java_lsp_benchmark_records_startup_failure(monkeypatch, tmp_path: Path) -> None:
    from sari.services.language_probe import java_lsp_benchmark as module

    monkeypatch.setattr(
        module,
        "discover_java_benchmark_targets",
        lambda repo_root: module.JavaBenchmarkTargets(
            document_symbol_paths=["src/main/java/com/example/BudgetController.java"],
            definition_request=module.SymbolRequest("src/main/java/com/example/BudgetController.java", 1, 1),
            reference_request=module.SymbolRequest("src/main/java/com/example/BudgetService.java", 1, 1),
            workspace_query="BudgetService",
        ),
    )

    class _FailingServer:
        def __init__(self, config, repo_root, settings):
            del config, repo_root, settings
            raise RuntimeError("startup failed")

    monkeypatch.setattr(module, "_provider_class", lambda provider: _FailingServer)

    observations = module.run_java_lsp_benchmark(repo_root=str(tmp_path), providers=("javalight",), repeats=1)

    assert len(observations) == 1
    assert observations[0].request_kind == "startup"
    assert observations[0].success is False
    assert observations[0].error == "startup failed"


def test_provider_env_preserves_existing_path(monkeypatch) -> None:
    from sari.services.language_probe import java_lsp_benchmark as module

    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")

    env = module._with_provider_env("javalight")

    assert env["PATH"] == "/opt/homebrew/bin:/usr/bin"
    assert "SARI_JAVA_LSP_PROVIDER" not in env
