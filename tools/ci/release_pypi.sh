#!/usr/bin/env bash
set -euo pipefail

# 프로젝트 루트를 기준으로 빌드/검증을 수행한다.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

# 배포 아티팩트는 항상 새로 생성한다.
rm -rf dist build

# 패키지 빌드 및 무결성 검사를 uv 환경에서 수행한다.
if ! command -v uv >/dev/null 2>&1; then
  echo "[release_pypi] uv command not found. Install uv first." >&2
  exit 1
fi

uv run --with build --with twine python -m build
uv run --with twine python -m twine check dist/*

echo "[release_pypi] build and twine check completed"
