#!/usr/bin/env bash
# Download prebuilt Slang (SystemVerilog parser) and svls (lint LSP) for Linux x86_64
# and install under backend/runtime/linux/eda-tools/bin for desktop bundling.
#
# Used by Red Hat and generic Linux packaging when the tools are not on PATH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUTPUT_DIR="${CHIPVERIFY_EDA_TOOLS_DIR:-${REPO_ROOT}/backend/runtime/linux/eda-tools/bin}"
CACHE_DIR="${CHIPVERIFY_EDA_TOOLS_CACHE:-${REPO_ROOT}/.packaging/eda-tools-cache}"

SLANG_VERSION="${CHIPVERIFY_SLANG_VERSION:-v11.0}"
SVLS_VERSION="${CHIPVERIFY_SVLS_VERSION:-v0.2.14}"

mkdir -p "${OUTPUT_DIR}" "${CACHE_DIR}"

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

find_single_executable() {
  local name="$1"
  local root="$2"
  local match
  match="$(find "${root}" -type f -name "${name}" -perm -111 2>/dev/null | head -1 || true)"
  if [[ -n "${match}" ]]; then
    printf '%s' "${match}"
    return 0
  fi
  match="$(find "${root}" -type f -name "${name}" 2>/dev/null | head -1 || true)"
  printf '%s' "${match}"
}

install_slang() {
  local dest="${OUTPUT_DIR}/slang"
  if [[ -x "${dest}" ]]; then
    echo "Slang already present: ${dest}"
    return 0
  fi

  need_cmd curl
  need_cmd tar

  local archive="${CACHE_DIR}/slang-linux-x86_64.tar.gz"
  local url="https://github.com/MikePopoloski/slang/releases/download/${SLANG_VERSION}/slang-linux-x86_64.tar.gz"
  if [[ ! -f "${archive}" ]]; then
    echo "Downloading Slang ${SLANG_VERSION}..."
    curl -fsSL "${url}" -o "${archive}"
  fi

  local extract_dir
  extract_dir="$(mktemp -d "${CACHE_DIR}/slang-extract.XXXXXX")"
  tar -xzf "${archive}" -C "${extract_dir}"
  local binary
  binary="$(find_single_executable slang "${extract_dir}")"
  if [[ -z "${binary}" || ! -f "${binary}" ]]; then
    echo "Slang binary not found inside ${archive}" >&2
    rm -rf "${extract_dir}"
    exit 1
  fi
  cp -f "${binary}" "${dest}"
  chmod +x "${dest}"
  rm -rf "${extract_dir}"
  echo "Installed Slang -> ${dest}"
  "${dest}" --version | head -1 || true
}

install_svls() {
  local dest="${OUTPUT_DIR}/svls"
  if [[ -x "${dest}" ]]; then
    echo "svls already present: ${dest}"
    return 0
  fi

  need_cmd curl
  need_cmd unzip

  local archive="${CACHE_DIR}/svls-${SVLS_VERSION}-x86_64-lnx.zip"
  local url="https://github.com/dalance/svls/releases/download/${SVLS_VERSION}/svls-${SVLS_VERSION}-x86_64-lnx.zip"
  if [[ ! -f "${archive}" ]]; then
    echo "Downloading svls ${SVLS_VERSION}..."
    curl -fsSL "${url}" -o "${archive}"
  fi

  local extract_dir
  extract_dir="$(mktemp -d "${CACHE_DIR}/svls-extract.XXXXXX")"
  unzip -q "${archive}" -d "${extract_dir}"
  local binary
  binary="$(find_single_executable svls "${extract_dir}")"
  if [[ -z "${binary}" || ! -f "${binary}" ]]; then
    echo "svls binary not found inside ${archive}" >&2
    rm -rf "${extract_dir}"
    exit 1
  fi
  cp -f "${binary}" "${dest}"
  chmod +x "${dest}"
  rm -rf "${extract_dir}"
  echo "Installed svls -> ${dest}"
  "${dest}" --version | head -1 || true
}

install_slang
install_svls

export CHIPVERIFY_SLANG_BIN="${OUTPUT_DIR}/slang"
export CHIPVERIFY_SVLS_BIN="${OUTPUT_DIR}/svls"
