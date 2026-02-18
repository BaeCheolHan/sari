#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET_REPO="${1:-${ROOT_DIR}}"

if [[ ! -d "${TARGET_REPO}" ]]; then
  echo "repo 경로가 존재하지 않습니다: ${TARGET_REPO}" >&2
  exit 1
fi

export SARI_MCP_PROBE_REPO="${TARGET_REPO}"
cd "${ROOT_DIR}"
python3 tools/ci/release_gate_mcp_probe.py call_flow
