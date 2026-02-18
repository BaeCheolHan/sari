#!/usr/bin/env bash
set -euo pipefail

# PR 하드게이트용 LSP matrix 실행 스크립트
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LANG_FILE="${ROOT_DIR}/tools/ci/lsp_required_languages.txt"
ARTIFACT_DIR="${ROOT_DIR}/artifacts/ci"
LOG_FILE="${ARTIFACT_DIR}/lsp-matrix-cli.log"
REPORT_FILE="${ARTIFACT_DIR}/lsp-matrix-report.json"
DIAGNOSE_JSON_FILE="${ARTIFACT_DIR}/lsp-matrix-diagnose.json"
DIAGNOSE_MD_FILE="${ARTIFACT_DIR}/lsp-matrix-diagnose.md"
SUMMARY_FILE="${ARTIFACT_DIR}/lsp-matrix-gate-summary.json"
DB_PATH="${ARTIFACT_DIR}/state.db"
REPO_FIXTURE="${ROOT_DIR}"
REPORT_ONLY="false"
GATE_MODE="hard"
REPAIR_SCRIPT="${ROOT_DIR}/tools/lsp/repair_missing_servers.sh"
RERUN_COUNT=0
REPAIR_APPLIED="false"
FINAL_GATE_DECISION="UNKNOWN"
RUN_ID=""
LSP_MATRIX_GATE_TIMEOUT_SEC="${SARI_LSP_MATRIX_GATE_TIMEOUT_SEC:-900}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --report-only)
      if [[ $# -lt 2 ]]; then
        echo "[LSP gate] --report-only requires true|false" >&2
        exit 1
      fi
      REPORT_ONLY="$(echo "$2" | tr '[:upper:]' '[:lower:]')"
      shift 2
      ;;
    *)
      echo "[LSP gate] unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "${ARTIFACT_DIR}"
rm -f "${LOG_FILE}" "${REPORT_FILE}" "${DIAGNOSE_JSON_FILE}" "${DIAGNOSE_MD_FILE}" "${SUMMARY_FILE}"

if [[ ! -f "${LANG_FILE}" ]]; then
  echo "[LSP gate] 언어 목록 파일이 없습니다: ${LANG_FILE}" >&2
  exit 1
fi

LANGS=()
while IFS= read -r value || [[ -n "${value}" ]]; do
  trimmed="$(echo "${value}" | tr -d '\r' | xargs)"
  if [[ -z "${trimmed}" || "${trimmed}" == \#* ]]; then
    continue
  fi
  LANGS+=("${trimmed}")
done < "${LANG_FILE}"

if [[ ${#LANGS[@]} -lt 35 ]]; then
  echo "[LSP gate] 필수 언어 수가 부족합니다: ${#LANGS[@]}" >&2
  exit 1
fi

export SARI_DB_PATH="${DB_PATH}"
export PYTHONPATH="${ROOT_DIR}/src"
rm -f "${DB_PATH}"

set +e
python3 -m sari.cli.main roots add "${REPO_FIXTURE}" >>"${LOG_FILE}" 2>&1
ROOTS_ADD_EXIT=$?
set -e
if [[ ${ROOTS_ADD_EXIT} -ne 0 ]]; then
  echo "[LSP gate] roots add 실패, 로그를 확인하세요: ${LOG_FILE}" >&2
  exit 1
fi

if [[ "${REPORT_ONLY}" == "true" ]]; then
  GATE_MODE="report-only"
fi

run_gate_once() {
  local run_exit=0
  local report_exit=0
  local diagnose_exit=0
  local strict_fail="true"
  if [[ "${GATE_MODE}" == "report-only" ]]; then
    strict_fail="false"
  fi
  RUN_ARGS=(
    -m sari.cli.main pipeline lsp-matrix run
    --repo "${REPO_FIXTURE}"
    --fail-on-unavailable "${strict_fail}"
    --strict-all-languages true
  )
  for lang in "${LANGS[@]}"; do
    RUN_ARGS+=(--required-language "${lang}")
  done
  set +e
  python3 - <<'PY' "${LOG_FILE}" "${LSP_MATRIX_GATE_TIMEOUT_SEC}" "${RUN_ARGS[@]}"
import subprocess
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
timeout_sec = float(sys.argv[2])
args = sys.argv[3:]
run_args = args
if len(args) > 0 and args[0] == "-m":
    run_args = [sys.executable, *args]

with log_path.open("a", encoding="utf-8") as handle:
    try:
        completed = subprocess.run(run_args, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout_sec, check=False)
        raise SystemExit(completed.returncode)
    except subprocess.TimeoutExpired:
        handle.write(f"[LSP gate] timeout exceeded: {timeout_sec} sec\n")
        handle.flush()
        raise SystemExit(124)
PY
  run_exit=$?
  set -e

  set +e
  python3 -m sari.cli.main pipeline lsp-matrix report --repo "${REPO_FIXTURE}" >"${REPORT_FILE}" 2>>"${LOG_FILE}"
  report_exit=$?
  set -e
  if [[ ${report_exit} -ne 0 ]]; then
    echo "[LSP gate] report 생성 실패, 로그를 확인하세요: ${LOG_FILE}" >&2
    return 1
  fi

  set +e
  python3 -m sari.cli.main pipeline lsp-matrix diagnose --repo "${REPO_FIXTURE}" --mode latest --output-dir "${ARTIFACT_DIR}" >>"${LOG_FILE}" 2>&1
  diagnose_exit=$?
  set -e
  if [[ ${diagnose_exit} -ne 0 ]]; then
    echo "[LSP gate] diagnose 생성 실패, 로그를 확인하세요: ${LOG_FILE}" >&2
    return 1
  fi
  return ${run_exit}
}

set +e
run_gate_once
RUN_EXIT=$?
set -e

MISSING_COUNT="$(python3 - <<'PY' "${DIAGNOSE_JSON_FILE}"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print(0)
    raise SystemExit(0)
payload = json.loads(path.read_text(encoding="utf-8"))
items = payload.get("missing_server_languages")
if isinstance(items, list):
    print(len(items))
else:
    print(0)
PY
)"

if [[ "${MISSING_COUNT}" != "0" ]]; then
  if [[ ! -x "${REPAIR_SCRIPT}" ]]; then
    echo "[LSP gate] 복구 스크립트 실행 권한이 없습니다: ${REPAIR_SCRIPT}" >&2
    if [[ "${GATE_MODE}" == "hard" ]]; then
      exit 1
    fi
  else
    set +e
    "${REPAIR_SCRIPT}" "${DIAGNOSE_JSON_FILE}" --apply >>"${LOG_FILE}" 2>&1
    REPAIR_EXIT=$?
    set -e
    if [[ ${REPAIR_EXIT} -eq 0 ]]; then
      REPAIR_APPLIED="true"
      RERUN_COUNT=1
      set +e
      run_gate_once
      RUN_EXIT=$?
      set -e
    elif [[ "${GATE_MODE}" == "hard" ]]; then
      echo "[LSP gate] 자동 복구 실행 실패, 로그를 확인하세요: ${LOG_FILE}" >&2
      exit 1
    fi
  fi
fi

python3 - <<'PY' "${REPORT_FILE}" "${LOG_FILE}" "${REPORT_ONLY}"
import json
import sys
from pathlib import Path

report_path = Path(sys.argv[1])
log_path = Path(sys.argv[2])
report_only = sys.argv[3].strip().lower() == "true"

payload = json.loads(report_path.read_text(encoding="utf-8"))
matrix = payload.get("lsp_matrix")
if not isinstance(matrix, dict):
    raise SystemExit("[LSP gate] report 형식 오류: lsp_matrix 누락")
gate = matrix.get("gate")
if not isinstance(gate, dict):
    raise SystemExit("[LSP gate] report 형식 오류: gate 누락")
passed = bool(gate.get("passed"))
if not passed:
    failed = gate.get("failed_required_languages")
    if report_only:
        print(f"[LSP gate] report-only mode: gate failed, failed_required_languages={failed}", file=sys.stderr)
        print(f"[LSP gate] 자세한 로그: {log_path}", file=sys.stderr)
    else:
        print(f"[LSP gate] gate failed, failed_required_languages={failed}", file=sys.stderr)
        print(f"[LSP gate] 자세한 로그: {log_path}", file=sys.stderr)
        raise SystemExit(1)
print("[LSP gate] gate passed")
PY

FINAL_GATE_DECISION="$(python3 - <<'PY' "${REPORT_FILE}"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
matrix = payload.get("lsp_matrix", {})
if isinstance(matrix, dict):
    gate = matrix.get("gate", {})
    if isinstance(gate, dict):
        print(str(gate.get("gate_decision", "UNKNOWN")))
        raise SystemExit(0)
print("UNKNOWN")
PY
)"

RUN_ID="$(python3 - <<'PY' "${REPORT_FILE}"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
matrix = payload.get("lsp_matrix", {})
if isinstance(matrix, dict):
    print(str(matrix.get("run_id", "")))
else:
    print("")
PY
)"

python3 - <<'PY' "${SUMMARY_FILE}" "${RUN_ID}" "${GATE_MODE}" "${REPAIR_APPLIED}" "${RERUN_COUNT}" "${FINAL_GATE_DECISION}" "${LOG_FILE}" "${REPORT_FILE}" "${DIAGNOSE_JSON_FILE}" "${DIAGNOSE_MD_FILE}"
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
payload = {
    "run_id": sys.argv[2],
    "gate_mode": sys.argv[3],
    "repair_applied": sys.argv[4].lower() == "true",
    "rerun_count": int(sys.argv[5]),
    "final_gate_decision": sys.argv[6],
    "artifacts": {
        "log": sys.argv[7],
        "report": sys.argv[8],
        "diagnose_json": sys.argv[9],
        "diagnose_markdown": sys.argv[10],
    },
}
summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

if [[ ${RUN_EXIT} -ne 0 && "${GATE_MODE}" == "hard" ]]; then
  echo "[LSP gate] run 단계에서 이미 실패가 감지되었습니다. 로그: ${LOG_FILE}" >&2
  exit 1
fi

if [[ ${RUN_EXIT} -ne 0 && "${GATE_MODE}" == "report-only" ]]; then
  echo "[LSP gate] report-only mode: run 실패를 보고만 하고 통과 처리합니다. 로그: ${LOG_FILE}" >&2
fi
