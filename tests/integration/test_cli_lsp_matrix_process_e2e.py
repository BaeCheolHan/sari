"""실제 subprocess 기반 CLI LSP 매트릭스 E2E를 검증한다."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from sari.core.language.registry import get_enabled_language_names
from sari.core.config import AppConfig
from sari.db.repositories.runtime_repository import RuntimeRepository
from sari.db.schema import init_schema
from sari.services.daemon.service import DaemonService


def _pick_free_port() -> int:
    """OS가 할당한 임시 가용 포트를 얻는다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _build_cli_env(home_dir: Path) -> dict[str, str]:
    """subprocess CLI 실행용 환경 변수를 구성한다."""
    env = os.environ.copy()
    src_root = str((Path(__file__).resolve().parents[2] / "src"))
    env["HOME"] = str(home_dir)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_root if existing_pythonpath == "" else f"{src_root}:{existing_pythonpath}"
    return env


def _run_cli(args: list[str], env: dict[str, str], timeout_sec: float = 30.0) -> subprocess.CompletedProcess[str]:
    """실제 CLI 명령을 subprocess로 실행한다."""
    return subprocess.run(
        [sys.executable, "-m", "sari"] + args,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )


def _parse_json_output(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """CLI 표준출력 JSON을 파싱한다."""
    return json.loads(result.stdout.strip())


def _wait_http_ready(url: str, timeout_sec: float = 6.0) -> None:
    """HTTP 엔드포인트 준비 완료까지 대기한다."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.3) as response:
                if int(response.status) == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise AssertionError(f"http not ready: {url}")


def _build_daemon_service(db_path: Path, preferred_port: int) -> DaemonService:
    """테스트용 데몬 서비스를 구성한다."""
    init_schema(db_path)
    config = AppConfig(
        db_path=db_path,
        host="127.0.0.1",
        preferred_port=preferred_port,
        max_port_scan=20,
        stop_grace_sec=10,
    )
    return DaemonService(config=config, runtime_repo=RuntimeRepository(db_path))


def test_cli_process_e2e_run_report_with_real_process(tmp_path: Path) -> None:
    """실제 CLI subprocess 실행으로 roots add/run/report 경로를 검증한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = tmp_path / "repo-a"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")

    env = _build_cli_env(home_dir=home_dir)

    add_result = _run_cli(["roots", "add", str(repo_dir.resolve())], env=env)
    assert add_result.returncode == 0, add_result.stderr

    run_result = _run_cli(
        [
            "pipeline",
            "lsp-matrix",
            "run",
            "--repo",
            str(repo_dir.resolve()),
            "--strict-all-languages",
            "false",
            "--fail-on-unavailable",
            "false",
        ],
        env=env,
    )
    assert run_result.returncode == 0, run_result.stderr
    run_payload = _parse_json_output(run_result)
    assert "lsp_matrix" in run_payload
    run_matrix = run_payload["lsp_matrix"]
    assert isinstance(run_matrix, dict)
    assert "gate" in run_matrix
    assert "summary" in run_matrix

    report_result = _run_cli(
        [
            "pipeline",
            "lsp-matrix",
            "report",
            "--repo",
            str(repo_dir.resolve()),
        ],
        env=env,
    )
    assert report_result.returncode == 0, report_result.stderr
    report_payload = _parse_json_output(report_result)
    assert "lsp_matrix" in report_payload
    report_matrix = report_payload["lsp_matrix"]
    assert isinstance(report_matrix, dict)
    assert "gate" in report_matrix


def test_cli_process_e2e_matches_http_report_after_daemon_start(tmp_path: Path) -> None:
    """CLI run 결과와 daemon HTTP report 결과의 게이트 값을 교차 검증한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = tmp_path / "repo-b"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "main.py").write_text("def hello_http():\n    return 2\n", encoding="utf-8")
    env = _build_cli_env(home_dir=home_dir)

    add_result = _run_cli(["roots", "add", str(repo_dir.resolve())], env=env)
    assert add_result.returncode == 0, add_result.stderr

    run_result = _run_cli(
        [
            "pipeline",
            "lsp-matrix",
            "run",
            "--repo",
            str(repo_dir.resolve()),
            "--strict-all-languages",
            "false",
            "--fail-on-unavailable",
            "false",
        ],
        env=env,
    )
    assert run_result.returncode == 0, run_result.stderr
    run_payload = _parse_json_output(run_result)
    matrix = run_payload["lsp_matrix"]
    assert isinstance(matrix, dict)
    gate = matrix["gate"]
    assert isinstance(gate, dict)

    db_path = home_dir / ".local" / "share" / "sari-v2" / "state.db"
    daemon_service = _build_daemon_service(db_path=db_path, preferred_port=_pick_free_port())
    runtime = daemon_service.start(run_mode="dev")
    host = str(runtime.host)
    port = int(runtime.port)
    try:
        _wait_http_ready(f"http://{host}:{port}/health")
        repo_q = urllib.parse.quote(str(repo_dir.resolve()))
        with urllib.request.urlopen(f"http://{host}:{port}/api/pipeline/lsp-matrix?repo={repo_q}", timeout=2.0) as response:
            http_payload = json.loads(response.read().decode("utf-8"))
        assert "lsp_matrix" in http_payload
        http_matrix = http_payload["lsp_matrix"]
        assert isinstance(http_matrix, dict)
        http_gate = http_matrix["gate"]
        assert isinstance(http_gate, dict)
        assert http_gate["gate_decision"] == gate["gate_decision"]
    finally:
        daemon_service.stop()


@pytest.mark.cli_e2e_lsp
def test_cli_process_e2e_real_lsp_all_languages_strict_gate(tmp_path: Path) -> None:
    """실LSP 환경에서 35+ 언어 strict 게이트를 실행하고 응답 계약을 검증한다."""
    if os.getenv("SARI_ENABLE_REAL_LSP_E2E", "").strip() != "1":
        pytest.skip("set SARI_ENABLE_REAL_LSP_E2E=1 to run real-lsp 35+ smoke")
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = tmp_path / "repo-c"
    repo_dir.mkdir(parents=True, exist_ok=True)
    # 전언어 strict 게이트를 실행하기 위해 최소 샘플 파일을 다수 생성한다.
    (repo_dir / "main.py").write_text("def p():\n    return 1\n", encoding="utf-8")
    (repo_dir / "main.ts").write_text("export const t = 1;\n", encoding="utf-8")
    (repo_dir / "Main.java").write_text("class Main {}\n", encoding="utf-8")
    (repo_dir / "Main.kt").write_text("fun main() {}\n", encoding="utf-8")
    (repo_dir / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (repo_dir / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (repo_dir / "main.cs").write_text("class Main {}\n", encoding="utf-8")

    env = _build_cli_env(home_dir=home_dir)
    add_result = _run_cli(["roots", "add", str(repo_dir.resolve())], env=env)
    assert add_result.returncode == 0, add_result.stderr

    run_result = _run_cli(
        [
            "pipeline",
            "lsp-matrix",
            "run",
            "--repo",
            str(repo_dir.resolve()),
            "--strict-all-languages",
            "true",
            "--fail-on-unavailable",
            "true",
        ],
        env=env,
        timeout_sec=120.0,
    )
    assert run_result.returncode == 0, run_result.stderr
    run_payload = _parse_json_output(run_result)
    assert "lsp_matrix" in run_payload
    matrix = run_payload["lsp_matrix"]
    assert isinstance(matrix, dict)
    gate = matrix["gate"]
    summary = matrix["summary"]
    assert isinstance(gate, dict)
    assert isinstance(summary, dict)
    assert gate["strict_all_languages"] is True
    assert gate["gate_decision"] == "PASS"
    assert float(summary["readiness_percent"]) >= 98.0
    assert int(summary["coverage_total_languages"]) == len(get_enabled_language_names())
