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

mkdir -p "${ARTIFACT_DIR}"
rm -f "${SUMMARY_FILE}" "${DB_PATH}" "${DAEMON_PROXY_LOG}" "${CLI_E2E_LOG}" "${CRITICAL_LSP_LOG}"

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
CRITICAL_LSP_PASSED="$(run_cmd critical_lsp "${CRITICAL_LSP_LOG}" bash -lc "python3 -m sari.cli.main roots add \"${REPO_FIXTURE}\" && python3 -m sari.cli.main pipeline lsp-matrix run --repo \"${REPO_FIXTURE}\" --required-language python --required-language typescript --required-language java --required-language kotlin --required-language go --required-language rust --required-language csharp --fail-on-unavailable true --strict-all-languages false --strict-symbol-gate true")"

FINAL_DECISION="PASS"
if [[ "${DAEMON_PROXY_PASSED}" != "true" || "${CLI_E2E_PASSED}" != "true" || "${CRITICAL_LSP_PASSED}" != "true" ]]; then
  FINAL_DECISION="FAIL"
fi

python3 - <<'PY' "${SUMMARY_FILE}" "${DAEMON_PROXY_PASSED}" "${CLI_E2E_PASSED}" "${CRITICAL_LSP_PASSED}" "${FINAL_DECISION}"
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
daemon_proxy_passed = sys.argv[2].lower() == "true"
cli_e2e_passed = sys.argv[3].lower() == "true"
critical_lsp_passed = sys.argv[4].lower() == "true"
final_decision = sys.argv[5]
release_gate_passed = daemon_proxy_passed and cli_e2e_passed and critical_lsp_passed
payload = {
    "release_gate_passed": release_gate_passed,
    "daemon_proxy_passed": daemon_proxy_passed,
    "cli_e2e_passed": cli_e2e_passed,
    "critical_lsp_passed": critical_lsp_passed,
    "final_decision": final_decision,
    "logs": {
        "daemon_proxy": str(Path(sys.argv[1]).parent / "release-gate-daemon-proxy.log"),
        "cli_e2e": str(Path(sys.argv[1]).parent / "release-gate-cli-e2e.log"),
        "critical_lsp": str(Path(sys.argv[1]).parent / "release-gate-critical-lsp.log"),
    },
}
summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

if [[ "${FINAL_DECISION}" != "PASS" ]]; then
  echo "[release gate] failed. see ${SUMMARY_FILE}" >&2
  exit 1
fi

echo "[release gate] passed"
