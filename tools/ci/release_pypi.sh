#!/usr/bin/env bash
set -euo pipefail

# 프로젝트 루트를 기준으로 빌드/검증을 수행한다.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

# 배포 아티팩트는 항상 새로 생성한다.
rm -rf dist build

# 패키지 빌드 및 무결성 검사를 수행한다.
python3 -m pip install --upgrade build twine
python3 -m build
python3 -m twine check dist/*

echo "[release_pypi] build and twine check completed"
