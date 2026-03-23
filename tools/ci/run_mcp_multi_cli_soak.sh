#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_DIR="${ROOT_DIR}/artifacts/ci"
LOG_FILE="${ARTIFACT_DIR}/mcp-multi-cli-soak.log"
SUMMARY_FILE="${ARTIFACT_DIR}/mcp-multi-cli-soak-summary.json"

mkdir -p "${ARTIFACT_DIR}"
rm -f "${LOG_FILE}" "${SUMMARY_FILE}"

export PYTHONPATH="${ROOT_DIR}/src"
RUN_ID="$(date +%s)"
export SARI_DB_PATH="${ARTIFACT_DIR}/mcp-multi-cli-soak-state-${RUN_ID}.db"
export SARI_MCP_PROBE_REPO="${SARI_MCP_PROBE_REPO:-${ROOT_DIR}}"
export SARI_MCP_SOAK_DURATION_SEC="${SOAK_DURATION_SEC:-1800}"
export SARI_MCP_SOAK_INTERVAL_SEC="${SOAK_INTERVAL_SEC:-1.0}"
export SARI_MCP_SOAK_MAX_FAILURE_RATE="${SOAK_MAX_FAILURE_RATE:-0.0}"
export SARI_MCP_SOAK_MAX_TIMEOUT_FAILURES="${SOAK_MAX_TIMEOUT_FAILURES:-0}"
export SARI_MCP_SOAK_MIN_ATTEMPTS="${SOAK_MIN_ATTEMPTS:-120}"
export SARI_MCP_SOAK_CLIENTS="${SOAK_CLIENTS:-4}"

set +e
python3 tools/ci/release_gate_mcp_probe.py soak | tee "${LOG_FILE}"
PROBE_EXIT_CODE=$?
set -e
export PROBE_EXIT_CODE

python3 - <<'PY' "${LOG_FILE}" "${SUMMARY_FILE}"
import json
import re
import subprocess
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
line = ""
for raw in reversed(log_path.read_text(encoding="utf-8", errors="replace").splitlines()):
    candidate = raw.strip()
    if candidate.startswith("PROBE_SUMMARY:"):
        line = candidate[len("PROBE_SUMMARY:") :].strip()
        break
if line == "":
    raise SystemExit("missing PROBE_SUMMARY in soak log")
probe = json.loads(line)
if not isinstance(probe, dict):
    raise SystemExit("invalid PROBE_SUMMARY payload")
detail = probe.get("detail")
if not isinstance(detail, dict):
    raise SystemExit("invalid soak detail payload")
attempts = int(detail.get("attempts", 0))
failures = int(detail.get("failures", 0))
failure_rate = float(detail.get("failure_rate", 1.0))
timeout_failures = int(detail.get("timeout_failures", 0))

ps = subprocess.run(
    ["ps", "-axo", "ppid=,pid=,stat=,command="],
    check=True,
    capture_output=True,
    text=True,
)
lines = ps.stdout.splitlines()
proc_pattern = re.compile(
    r"(sari|solidlsp|pyrefly|gopls|rust-analyzer|jdtls|typescript-language-server|clangd|lua-language-server)",
    re.IGNORECASE,
)
zombie_count = 0
orphan_count = 0
for row in lines:
    row = row.strip()
    if row == "":
        continue
    match = re.match(r"^(\d+)\s+(\d+)\s+([A-Za-z]+)\s+(.*)$", row)
    if match is None:
        continue
    ppid = int(match.group(1))
    stat = match.group(3)
    command = match.group(4)
    if proc_pattern.search(command) is None:
        continue
    if stat.upper().startswith("Z"):
        zombie_count += 1
    if ppid == 1:
        orphan_count += 1

probe_exit_code = int(__import__("os").environ.get("PROBE_EXIT_CODE", "1"))
pass_flag = probe_exit_code == 0 and bool(probe.get("ok", False)) and attempts > 0 and failures == 0 and failure_rate == 0.0 and timeout_failures == 0 and zombie_count == 0 and orphan_count == 0
payload = {
    "pass": pass_flag,
    "probe_exit_code": probe_exit_code,
    "attempts": attempts,
    "failures": failures,
    "failure_rate": failure_rate,
    "timeout_failures": timeout_failures,
    "orphan_count": orphan_count,
    "zombie_count": zombie_count,
    "probe": probe,
}
summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
if not pass_flag:
    raise SystemExit("mcp multi-cli soak failed")
PY

echo "[mcp-soak] passed"
