#!/usr/bin/env bash
set -euo pipefail

ROOT_PATH="${1:-}"
MAX_GLIBC_VERSION="${2:-}"

if [[ -z "${ROOT_PATH}" || ! -e "${ROOT_PATH}" ]]; then
  echo "Usage: $0 <elf-file-or-directory> [max-glibc-version]" >&2
  exit 2
fi

if ! command -v grep >/dev/null 2>&1; then
  echo "grep is required to inspect Linux runtime compatibility." >&2
  exit 2
fi

version_gt() {
  local left="$1"
  local right="$2"
  [[ "${left}" != "${right}" && "$(printf '%s\n%s\n' "${left}" "${right}" | sort -V | tail -n 1)" == "${left}" ]]
}

tmp_results="$(mktemp)"
tmp_failures="$(mktemp)"
tmp_candidates="$(mktemp)"
tmp_matches="$(mktemp)"
trap 'rm -f "${tmp_results}" "${tmp_failures}" "${tmp_candidates}" "${tmp_matches}"' EXIT

(
  if [[ -d "${ROOT_PATH}" ]]; then
    find "${ROOT_PATH}" -type f \( \
      -name '*.so' \
      -o -name '*.so.*' \
      -o -name '*.cpython-*.so' \
      -o -name 'chipverify-backend' \
    \) -print0
  else
    printf '%s\0' "${ROOT_PATH}"
  fi
) > "${tmp_candidates}"

if [[ -s "${tmp_candidates}" ]]; then
  LC_ALL=C xargs -0 grep -aHoE 'GLIBC_[0-9]+(\.[0-9]+)*' \
    < "${tmp_candidates}" > "${tmp_matches}" 2>/dev/null || true
fi

while IFS= read -r match; do
  version="${match##*:GLIBC_}"
  candidate="${match%:GLIBC_${version}}"
  printf '%s\t%s\n' "${version}" "${candidate}"
done < "${tmp_matches}" \
  | sort -k2,2 -k1,1V \
  | awk -F '\t' '{ latest[$2] = $1 } END { for (path in latest) print latest[path] "\t" path }' \
  > "${tmp_results}"

while IFS=$'\t' read -r required candidate; do
  if [[ -n "${MAX_GLIBC_VERSION}" ]] && version_gt "${required}" "${MAX_GLIBC_VERSION}"; then
    printf '%s\t%s\n' "${required}" "${candidate}" >> "${tmp_failures}"
  fi
done < "${tmp_results}"

if [[ ! -s "${tmp_results}" ]]; then
  echo "No ELF files with GLIBC version requirements were found under ${ROOT_PATH}."
  exit 0
fi

overall_max="$(sort -k1,1V "${tmp_results}" | tail -n 1 | cut -f1)"
echo "Maximum required GLIBC version under ${ROOT_PATH}: ${overall_max}"

if [[ -z "${MAX_GLIBC_VERSION}" ]]; then
  sort -k1,1V "${tmp_results}" | tail -n 20
  exit 0
fi

echo "Target maximum GLIBC version: ${MAX_GLIBC_VERSION}"
if [[ -s "${tmp_failures}" ]]; then
  echo "Linux runtime is not compatible with target GLIBC ${MAX_GLIBC_VERSION}." >&2
  echo "Files requiring a newer GLIBC:" >&2
  sort -k1,1V "${tmp_failures}" | tail -n 40 >&2
  exit 1
fi

echo "GLIBC compatibility check passed."
