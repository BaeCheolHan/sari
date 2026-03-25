#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_DIR="${ROOT_DIR}/artifacts/ci"
SUMMARY_FILE="${ARTIFACT_DIR}/release-gate-summary.json"
DB_PATH="${ARTIFACT_DIR}/release-gate-state.db"
REPO_FIXTURE="${ARTIFACT_DIR}/release-gate-critical-fixture"
DAEMON_PROXY_LOG="${ARTIFACT_DIR}/release-gate-daemon-proxy.log"
CLI_E2E_LOG="${ARTIFACT_DIR}/release-gate-cli-e2e.log"
CRITICAL_LSP_LOG="${ARTIFACT_DIR}/release-gate-critical-lsp.log"
MCP_HANDSHAKE_LOG="${ARTIFACT_DIR}/release-gate-mcp-handshake.log"
MCP_CONCURRENCY_LOG="${ARTIFACT_DIR}/release-gate-mcp-concurrency.log"
MCP_CALL_FLOW_LOG="${ARTIFACT_DIR}/release-gate-mcp-call-flow.log"
QUEUE_OPS_LOG="${ARTIFACT_DIR}/release-gate-queue-ops.log"
RECONCILE_LOG="${ARTIFACT_DIR}/release-gate-reconcile.log"

mkdir -p "${ARTIFACT_DIR}"
rm -f "${SUMMARY_FILE}" "${DB_PATH}" "${DAEMON_PROXY_LOG}" "${CLI_E2E_LOG}" "${CRITICAL_LSP_LOG}" "${MCP_HANDSHAKE_LOG}" "${MCP_CONCURRENCY_LOG}" "${MCP_CALL_FLOW_LOG}" "${QUEUE_OPS_LOG}" "${RECONCILE_LOG}"

prepare_critical_fixture() {
  rm -rf "${REPO_FIXTURE}"
  mkdir -p "${REPO_FIXTURE}"
  cat >"${REPO_FIXTURE}/main.py" <<'EOF'
def hello(name: str) -> str:
    return f"hello {name}"
EOF
  cat >"${REPO_FIXTURE}/main.ts" <<'EOF'
export function hello(name: string): string {
  return `hello ${name}`;
}
EOF
  cat >"${REPO_FIXTURE}/Main.java" <<'EOF'
public class Main {
    public static String hello(String name) {
        return "hello " + name;
    }
}
EOF
  cat >"${REPO_FIXTURE}/Main.kt" <<'EOF'
class Main {
    fun hello(name: String): String = "hello $name"
}
EOF
  cat >"${REPO_FIXTURE}/main.go" <<'EOF'
package main

func hello(name string) string {
	return "hello " + name
}
EOF
  cat >"${REPO_FIXTURE}/main.rs" <<'EOF'
fn hello(name: &str) -> String {
    format!("hello {}", name)
}
EOF
  cat >"${REPO_FIXTURE}/Program.cs" <<'EOF'
public class Program {
    public static string Hello(string name) {
        return "hello " + name;
    }
}
EOF
  cat >"${REPO_FIXTURE}/sari.csproj" <<'EOF'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
EOF
}

export PYTHONPATH="${ROOT_DIR}/src"
export SARI_DB_PATH="${DB_PATH}"
prepare_critical_fixture

python3 "${ROOT_DIR}/tools/ci/check_no_legacy_shim_imports.py" "${ROOT_DIR}/src" "${ROOT_DIR}/tests"
# call_flow probe는 fresh DB에서도 deterministic 하게 동작해야 하므로
# probe 대상 repo를 먼저 roots/index에 반영한다.
python3 -m sari.cli.main roots add "${ROOT_DIR}" >/dev/null 2>&1 || true
python3 -m sari.cli.main index >/dev/null

run_cmd() {
  local label="$1"
  local log_file="$2"
  shift
  shift
  set +e
  "$@" >"${log_file}" 2>&1
  local exit_code=$?
  set -e
  if [[ ${exit_code} -eq 0 ]]; then
    echo "true"
  else
    echo "false"
  fi
}

DAEMON_PROXY_PASSED="$(run_cmd daemon_proxy "${DAEMON_PROXY_LOG}" pytest -q tests/unit/mcp/test_mcp_daemon_forward.py tests/unit/mcp/test_mcp_server_protocol.py tests/unit/daemon/test_daemon_resolver_and_proxy.py tests/unit/misc/test_package_version_sync.py)"
CLI_E2E_PASSED="$(run_cmd cli_e2e "${CLI_E2E_LOG}" bash -lc "python3 -m sari.cli.main --help && python3 -m sari.cli.main mcp stdio --help && python3 -m sari.cli.main daemon --help")"
CRITICAL_LSP_PASSED="$(run_cmd critical_lsp "${CRITICAL_LSP_LOG}" bash -lc "tools/ci/run_lsp_matrix_gate.sh --report-only true")"
MCP_HANDSHAKE_PASSED="$(run_cmd mcp_handshake "${MCP_HANDSHAKE_LOG}" python3 tools/ci/release_gate_mcp_probe.py handshake)"
MCP_CONCURRENCY_PASSED="$(run_cmd mcp_concurrency "${MCP_CONCURRENCY_LOG}" python3 tools/ci/release_gate_mcp_probe.py concurrency)"
MCP_CALL_FLOW_PASSED="$(
  run_cmd mcp_call_flow "${MCP_CALL_FLOW_LOG}" env \
    SARI_MCP_PROBE_REPO="${ROOT_DIR}" \
    SARI_MCP_PROBE_QUERY="status_endpoint" \
    SARI_MCP_PROBE_SYMBOL="status_endpoint" \
    SARI_MCP_PROBE_EXPECT_CALLERS_MIN="0" \
    python3 tools/ci/release_gate_mcp_probe.py call_flow
)"
QUEUE_OPS_PASSED="$(run_cmd queue_ops "${QUEUE_OPS_LOG}" bash -lc "python3 -m sari.cli.main pipeline dead list --repo '${REPO_FIXTURE}' --limit 5 && python3 -m sari.cli.main pipeline dead requeue --repo '${REPO_FIXTURE}' --limit 5 && python3 -m sari.cli.main pipeline dead purge --repo '${REPO_FIXTURE}' --limit 5 --confirm")"
RECONCILE_PASSED="$(run_cmd reconcile "${RECONCILE_LOG}" python3 - "${REPO_FIXTURE}" <<'PY'
import json
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError
import time

import subprocess

repo = sys.argv[1]

def run_ensure() -> dict[str, object]:
    ensure = subprocess.run(
        [sys.executable, "-m", "sari.cli.main", "daemon", "ensure", "--run-mode", "prod"],
        check=False,
        capture_output=True,
        text=True,
    )
    if ensure.returncode != 0:
        raise SystemExit(f"daemon ensure failed: {ensure.stderr.strip()}")
    try:
        loaded = json.loads(ensure.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"daemon ensure json parse failed: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SystemExit("daemon ensure payload is not object")
    return loaded


def wait_until_health(current_host: str, current_port: int) -> None:
    health_url = f"http://{current_host}:{current_port}/health"
    last_error: Exception | None = None
    for _ in range(10):
        try:
            with urlopen(health_url, timeout=3.0):
                return
        except URLError as exc:
            last_error = exc
            time.sleep(0.5)
    raise SystemExit(f"daemon health wait failed: {last_error}")

ensure_payload = run_ensure()
daemon = ensure_payload.get("daemon")
if not isinstance(daemon, dict):
    raise SystemExit("daemon ensure payload missing daemon")
host = str(daemon.get("host", "127.0.0.1"))
port = int(daemon.get("port", 47777))
wait_until_health(host, port)
def resolve_reconcile_payload() -> tuple[str, dict[str, object]]:
    current_host = host
    current_port = port
    last_error: Exception | None = None
    for _attempt in range(8):
        health_url = f"http://{current_host}:{current_port}/health"
        reconcile_url = f"http://{current_host}:{current_port}/daemon/reconcile"
        try:
            with urlopen(health_url, timeout=3.0):
                pass
            req = Request(reconcile_url, method="POST")
            with urlopen(req, timeout=5.0) as resp:
                loaded = json.loads(resp.read().decode("utf-8"))
            if not isinstance(loaded, dict):
                raise SystemExit("reconcile payload is not object")
            return reconcile_url, loaded
        except URLError as exc:
            last_error = exc
            _ = subprocess.run([sys.executable, "-m", "sari.cli.main", "daemon", "stop"], check=False, capture_output=True, text=True)
            ensure_payload_retry = run_ensure()
            daemon_retry = ensure_payload_retry.get("daemon")
            if not isinstance(daemon_retry, dict):
                raise SystemExit("daemon ensure payload missing daemon after retry")
            current_host = str(daemon_retry.get("host", "127.0.0.1"))
            current_port = int(daemon_retry.get("port", 47777))
            wait_until_health(current_host, current_port)
    raise SystemExit(f"reconcile request failed after retry: {last_error}")

reconcile_url, payload = resolve_reconcile_payload()
result = payload.get("result")
if not isinstance(result, dict):
    raise SystemExit("reconcile result missing")
required_keys = (
    "reconciled_daemons",
    "reaped_lsp",
    "reaped_lsp_by_language",
    "drain_failures",
    "orphan_workers_stopped",
    "stale_registry_cleaned",
)
for key in required_keys:
    if key == "reaped_lsp_by_language":
        breakdown = result.get(key, {})
        if not isinstance(breakdown, dict) or len(breakdown) != 0:
            raise SystemExit(f"reconcile strict-zero failed: {result}")
        continue
    if int(result.get(key, 0)) != 0:
        raise SystemExit(f"reconcile strict-zero failed: {result}")
print(json.dumps({"url": reconcile_url, "result": result}, ensure_ascii=False))
_ = subprocess.run([sys.executable, "-m", "sari.cli.main", "daemon", "stop"], check=False, capture_output=True, text=True)
PY
)"

python3 - <<'PY' "${SUMMARY_FILE}" "${DAEMON_PROXY_PASSED}" "${CLI_E2E_PASSED}" "${CRITICAL_LSP_PASSED}" "${MCP_HANDSHAKE_PASSED}" "${MCP_CONCURRENCY_PASSED}" "${MCP_CALL_FLOW_PASSED}" "${QUEUE_OPS_PASSED}" "${RECONCILE_PASSED}" "${MCP_HANDSHAKE_LOG}" "${MCP_CONCURRENCY_LOG}" "${MCP_CALL_FLOW_LOG}" "${RECONCILE_LOG}"
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
daemon_proxy_passed = sys.argv[2].lower() == "true"
cli_e2e_passed = sys.argv[3].lower() == "true"
critical_lsp_passed = sys.argv[4].lower() == "true"
mcp_handshake_passed = sys.argv[5].lower() == "true"
mcp_concurrency_passed = sys.argv[6].lower() == "true"
mcp_call_flow_passed = sys.argv[7].lower() == "true"
queue_ops_passed = sys.argv[8].lower() == "true"
reconcile_passed = sys.argv[9].lower() == "true"
handshake_log_path = Path(sys.argv[10])
concurrency_log_path = Path(sys.argv[11])
call_flow_log_path = Path(sys.argv[12])
reconcile_log_path = Path(sys.argv[13])
release_gate_passed = daemon_proxy_passed and cli_e2e_passed and critical_lsp_passed and mcp_handshake_passed and mcp_concurrency_passed and mcp_call_flow_passed and queue_ops_passed and reconcile_passed


def extract_probe_summary(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    for raw_line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = raw_line.strip()
        if not line.startswith("PROBE_SUMMARY:"):
            continue
        payload = line[len("PROBE_SUMMARY:") :].strip()
        if payload == "":
            continue
        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError:
            return {"parse_error": "invalid_probe_summary_json", "raw": payload}
        if isinstance(loaded, dict):
            return loaded
        return {"parse_error": "probe_summary_not_dict", "raw": payload}
    return None


handshake_probe_summary = extract_probe_summary(handshake_log_path)
concurrency_probe_summary = extract_probe_summary(concurrency_log_path)
call_flow_probe_summary = extract_probe_summary(call_flow_log_path)
reconcile_summary = None
if reconcile_log_path.exists():
    lines = reconcile_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > 0:
        try:
            reconcile_summary = json.loads(lines[-1])
        except json.JSONDecodeError:
            reconcile_summary = {"parse_error": "invalid_reconcile_json", "raw": lines[-1]}


def validate_probe_summary(name: str, summary: object) -> list[str]:
    if not isinstance(summary, dict):
        return [f"{name}: missing_or_invalid_summary"]
    errors: list[str] = []
    if "ok" not in summary:
        errors.append(f"{name}: missing_ok")
    if "detail" not in summary:
        errors.append(f"{name}: missing_detail")
    if "mode" not in summary:
        errors.append(f"{name}: missing_mode")
    return errors


probe_validation_errors: list[str] = []
probe_validation_errors.extend(validate_probe_summary("mcp_handshake", handshake_probe_summary))
probe_validation_errors.extend(validate_probe_summary("mcp_concurrency", concurrency_probe_summary))
probe_validation_errors.extend(validate_probe_summary("mcp_call_flow", call_flow_probe_summary))
probe_details_valid = len(probe_validation_errors) == 0
final_decision = "PASS" if release_gate_passed and probe_details_valid else "FAIL"
payload = {
    "release_gate_passed": release_gate_passed,
    "probe_details_valid": probe_details_valid,
    "probe_validation_errors": probe_validation_errors,
    "daemon_proxy_passed": daemon_proxy_passed,
    "cli_e2e_passed": cli_e2e_passed,
    "critical_lsp_passed": critical_lsp_passed,
    "mcp_handshake_passed": mcp_handshake_passed,
    "mcp_concurrency_passed": mcp_concurrency_passed,
    "mcp_call_flow_passed": mcp_call_flow_passed,
    "queue_ops_passed": queue_ops_passed,
    "reconcile_passed": reconcile_passed,
    "final_decision": final_decision,
    "logs": {
        "daemon_proxy": str(Path(sys.argv[1]).parent / "release-gate-daemon-proxy.log"),
        "cli_e2e": str(Path(sys.argv[1]).parent / "release-gate-cli-e2e.log"),
        "critical_lsp": str(Path(sys.argv[1]).parent / "release-gate-critical-lsp.log"),
        "mcp_handshake": str(Path(sys.argv[1]).parent / "release-gate-mcp-handshake.log"),
        "mcp_concurrency": str(Path(sys.argv[1]).parent / "release-gate-mcp-concurrency.log"),
        "mcp_call_flow": str(Path(sys.argv[1]).parent / "release-gate-mcp-call-flow.log"),
        "queue_ops": str(Path(sys.argv[1]).parent / "release-gate-queue-ops.log"),
        "reconcile": str(Path(sys.argv[1]).parent / "release-gate-reconcile.log"),
    },
    "probe_details": {
        "mcp_handshake": handshake_probe_summary,
        "mcp_concurrency": concurrency_probe_summary,
        "mcp_call_flow": call_flow_probe_summary,
        "reconcile": reconcile_summary,
    },
}
summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

FINAL_DECISION="$(python3 - <<'PY' "${SUMMARY_FILE}"
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(str(summary.get("final_decision", "FAIL")))
PY
)"

if [[ "${FINAL_DECISION}" != "PASS" ]]; then
  echo "[release gate] failed. see ${SUMMARY_FILE}" >&2
  if [[ -f "${SUMMARY_FILE}" ]]; then
    echo "[release gate] summary:" >&2
    cat "${SUMMARY_FILE}" >&2 || true
  fi
  for path in "${DAEMON_PROXY_LOG}" "${CLI_E2E_LOG}" "${CRITICAL_LSP_LOG}" "${MCP_HANDSHAKE_LOG}" "${MCP_CONCURRENCY_LOG}" "${MCP_CALL_FLOW_LOG}" "${QUEUE_OPS_LOG}" "${RECONCILE_LOG}"; do
    if [[ -f "${path}" ]]; then
      echo "[release gate] tail ${path}:" >&2
      tail -n 80 "${path}" >&2 || true
    fi
  done
  exit 1
fi

echo "[release gate] passed"
