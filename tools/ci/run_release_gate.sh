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

mkdir -p "${ARTIFACT_DIR}"
rm -f "${SUMMARY_FILE}" "${DB_PATH}" "${DAEMON_PROXY_LOG}" "${CLI_E2E_LOG}" "${CRITICAL_LSP_LOG}" "${MCP_HANDSHAKE_LOG}" "${MCP_CONCURRENCY_LOG}"

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

DAEMON_PROXY_PASSED="$(run_cmd daemon_proxy "${DAEMON_PROXY_LOG}" pytest -q tests/unit/test_mcp_daemon_forward.py tests/unit/test_mcp_server_protocol.py tests/unit/test_daemon_resolver_and_proxy.py)"
CLI_E2E_PASSED="$(run_cmd cli_e2e "${CLI_E2E_LOG}" bash -lc "python3 -m sari.cli.main --help && python3 -m sari.cli.main mcp stdio --help && python3 -m sari.cli.main daemon --help")"
CRITICAL_LSP_PASSED="$(run_cmd critical_lsp "${CRITICAL_LSP_LOG}" bash -lc "tools/ci/run_lsp_matrix_gate.sh --report-only true")"
MCP_HANDSHAKE_PASSED="$(run_cmd mcp_handshake "${MCP_HANDSHAKE_LOG}" python3 tools/ci/release_gate_mcp_probe.py handshake)"
MCP_CONCURRENCY_PASSED="$(run_cmd mcp_concurrency "${MCP_CONCURRENCY_LOG}" python3 tools/ci/release_gate_mcp_probe.py concurrency)"

FINAL_DECISION="PASS"
if [[ "${DAEMON_PROXY_PASSED}" != "true" || "${CLI_E2E_PASSED}" != "true" || "${CRITICAL_LSP_PASSED}" != "true" || "${MCP_HANDSHAKE_PASSED}" != "true" || "${MCP_CONCURRENCY_PASSED}" != "true" ]]; then
  FINAL_DECISION="FAIL"
fi

python3 - <<'PY' "${SUMMARY_FILE}" "${DAEMON_PROXY_PASSED}" "${CLI_E2E_PASSED}" "${CRITICAL_LSP_PASSED}" "${MCP_HANDSHAKE_PASSED}" "${MCP_CONCURRENCY_PASSED}" "${FINAL_DECISION}"
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
daemon_proxy_passed = sys.argv[2].lower() == "true"
cli_e2e_passed = sys.argv[3].lower() == "true"
critical_lsp_passed = sys.argv[4].lower() == "true"
mcp_handshake_passed = sys.argv[5].lower() == "true"
mcp_concurrency_passed = sys.argv[6].lower() == "true"
final_decision = sys.argv[7]
release_gate_passed = daemon_proxy_passed and cli_e2e_passed and critical_lsp_passed and mcp_handshake_passed and mcp_concurrency_passed
payload = {
    "release_gate_passed": release_gate_passed,
    "daemon_proxy_passed": daemon_proxy_passed,
    "cli_e2e_passed": cli_e2e_passed,
    "critical_lsp_passed": critical_lsp_passed,
    "mcp_handshake_passed": mcp_handshake_passed,
    "mcp_concurrency_passed": mcp_concurrency_passed,
    "final_decision": final_decision,
    "logs": {
        "daemon_proxy": str(Path(sys.argv[1]).parent / "release-gate-daemon-proxy.log"),
        "cli_e2e": str(Path(sys.argv[1]).parent / "release-gate-cli-e2e.log"),
        "critical_lsp": str(Path(sys.argv[1]).parent / "release-gate-critical-lsp.log"),
        "mcp_handshake": str(Path(sys.argv[1]).parent / "release-gate-mcp-handshake.log"),
        "mcp_concurrency": str(Path(sys.argv[1]).parent / "release-gate-mcp-concurrency.log"),
    },
}
summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

if [[ "${FINAL_DECISION}" != "PASS" ]]; then
  echo "[release gate] failed. see ${SUMMARY_FILE}" >&2
  if [[ -f "${SUMMARY_FILE}" ]]; then
    echo "[release gate] summary:" >&2
    cat "${SUMMARY_FILE}" >&2 || true
  fi
  for path in "${DAEMON_PROXY_LOG}" "${CLI_E2E_LOG}" "${CRITICAL_LSP_LOG}" "${MCP_HANDSHAKE_LOG}" "${MCP_CONCURRENCY_LOG}"; do
    if [[ -f "${path}" ]]; then
      echo "[release gate] tail ${path}:" >&2
      tail -n 80 "${path}" >&2 || true
    fi
  done
  exit 1
fi

echo "[release gate] passed"
