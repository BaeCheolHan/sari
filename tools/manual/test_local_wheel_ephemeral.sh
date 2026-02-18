#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist"

if [[ $# -ge 1 ]]; then
  WHEEL_PATH="$1"
else
  WHEEL_PATH="$(ls -1t "${DIST_DIR}"/sari-*.whl 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "${WHEEL_PATH}" || ! -f "${WHEEL_PATH}" ]]; then
  echo "wheel 파일을 찾지 못했습니다. 먼저 python3 -m build 를 실행하세요." >&2
  exit 1
fi

echo "[local-wheel] using ${WHEEL_PATH}"
uvx --from "${WHEEL_PATH}" sari doctor
uvx --from "${WHEEL_PATH}" sari --help >/dev/null
echo "[local-wheel] ok (global uv tool 미변경)"
