#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${ROOT_DIR}/app/engine_tokenizer_data"

if [[ ! -d "${DATA_DIR}" ]]; then
  echo "engine_tokenizer_data not found: ${DATA_DIR}"
  exit 1
fi

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m | tr '[:upper:]' '[:lower:]')"

TAG=""
if [[ "${OS}" == "darwin" ]]; then
  if [[ "${ARCH}" == "arm64" || "${ARCH}" == "aarch64" ]]; then
    TAG="macosx_11_0_arm64"
  else
    TAG="macosx_10_9_x86_64"
  fi
elif [[ "${OS}" == "linux" ]]; then
  if [[ "${ARCH}" == "aarch64" || "${ARCH}" == "arm64" ]]; then
    TAG="manylinux_2_17_aarch64"
  else
    TAG="manylinux_2_17_x86_64"
  fi
elif [[ "${OS}" == "mingw"* || "${OS}" == "msys"* || "${OS}" == "cygwin"* ]]; then
  TAG="win_amd64"
else
  echo "Unsupported OS for pruning: ${OS}"
  exit 1
fi

echo "Keeping tokenizer bundle tag: ${TAG}"
for f in "${DATA_DIR}"/lindera_python_ipadic-*.whl; do
  [[ -e "${f}" ]] || continue
  if [[ "${f}" != *"${TAG}"* ]]; then
    echo "Removing ${f}"
    rm -f "${f}"
  fi
done

echo "Done."
