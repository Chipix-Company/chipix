#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

BACKEND_DIST="${CHIPVERIFY_REDHAT_BACKEND_DIST:-${REPO_ROOT}/backend/runtime/redhat/build/dist/chipverify-backend}"
TARGET="${1:-AppImage}"
REDHAT_GLIBC_MAX="${CHIPVERIFY_REDHAT_GLIBC_MAX:-2.28}"
STAGE_ROOT="${REPO_ROOT}/build-resources/runtime"
STAGE_BACKEND="${STAGE_ROOT}/backend"
STAGE_BIN="${STAGE_ROOT}/bin"
DIST_REDhat="${REPO_ROOT}/dist-redhat"
GLIBC_COMPAT_SCRIPT="${REPO_ROOT}/scripts/packaging/check_linux_glibc_compat.sh"

case "${TARGET}" in
  AppImage|rpm|all) ;;
  *)
    echo "Usage: $0 [AppImage|rpm|all]" >&2
    exit 2
    ;;
esac

if [[ ! -x "${BACKEND_DIST}/chipverify-backend" ]]; then
  echo "Red Hat backend bundle is missing: ${BACKEND_DIST}/chipverify-backend" >&2
  exit 1
fi

cd "${REPO_ROOT}"

cleanup_stage() {
  rm -rf "${STAGE_ROOT}"
  mkdir -p "${STAGE_ROOT}"
  touch "${STAGE_ROOT}/.gitkeep"
}
trap cleanup_stage EXIT

echo "[1/5] Verifying prebuilt Red Hat backend GLIBC compatibility"
bash "${GLIBC_COMPAT_SCRIPT}" "${BACKEND_DIST}" "${REDHAT_GLIBC_MAX}"
"${BACKEND_DIST}/chipverify-backend" --self-test-graphify

echo "[2/5] Staging Electron runtime resources"
rm -rf "${STAGE_ROOT}"
mkdir -p "${STAGE_BACKEND}" "${STAGE_BIN}"
cp -a "${BACKEND_DIST}/." "${STAGE_BACKEND}/"

SECRET_KEY="$(node -e 'console.log(require("crypto").randomBytes(32).toString("hex"))')"
export CHIPVERIFY_SECRET_KEY="${SECRET_KEY}"
STAGED_ENV="${STAGE_BACKEND}/.env"
SOURCE_ENV="${REPO_ROOT}/backend/.env"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/stage_embedded_runtime_env.sh"
export PYTHON_BIN="${CHIPVERIFY_PYTHON_BIN:-python3}"
export CHIPVERIFY_LLM_PROVIDER="${CHIPVERIFY_LLM_PROVIDER:-bedrock}"
export MODEL_PROVIDER="${MODEL_PROVIDER:-${CHIPVERIFY_LLM_PROVIDER}}"
export CHIPVERIFY_REQUIRE_PACKAGED_LLM_KEY="${CHIPVERIFY_REQUIRE_PACKAGED_LLM_KEY:-true}"
stage_embedded_runtime_env "${SOURCE_ENV}"

if [[ -f "${REPO_ROOT}/backend/license.key" ]]; then
  cp "${REPO_ROOT}/backend/license.key" "${STAGE_BACKEND}/license.key"
fi

resolve_tool() {
  local env_value="$1"
  local command_name="$2"
  if [[ -n "${env_value}" && -x "${env_value}" ]]; then
    printf '%s' "${env_value}"
    return 0
  fi
  command -v "${command_name}" 2>/dev/null || true
}

EDA_TOOLS_BIN="${REPO_ROOT}/backend/runtime/linux/eda-tools/bin"
if [[ ! -x "${EDA_TOOLS_BIN}/slang" || ! -x "${EDA_TOOLS_BIN}/svls" ]]; then
  echo "Fetching Slang + svls for TruthCore and RTL lint"
  bash "${SCRIPT_DIR}/fetch_linux_eda_tools.sh"
fi

if [[ -z "${CHIPVERIFY_SLANG_BIN:-}" && -x "${EDA_TOOLS_BIN}/slang" ]]; then
  export CHIPVERIFY_SLANG_BIN="${EDA_TOOLS_BIN}/slang"
fi
if [[ -z "${CHIPVERIFY_SVLS_BIN:-}" && -x "${EDA_TOOLS_BIN}/svls" ]]; then
  export CHIPVERIFY_SVLS_BIN="${EDA_TOOLS_BIN}/svls"
fi

SLANG_SOURCE="$(resolve_tool "${CHIPVERIFY_SLANG_BIN:-}" slang)"
SVLS_SOURCE="$(resolve_tool "${CHIPVERIFY_SVLS_BIN:-}" svls)"

if [[ -n "${SLANG_SOURCE}" ]]; then
  cp "${SLANG_SOURCE}" "${STAGE_BIN}/slang"
  chmod +x "${STAGE_BIN}/slang"
  echo "Bundled Slang: ${SLANG_SOURCE}"
  "${STAGE_BIN}/slang" --version | head -1 || true
else
  echo "WARNING: Slang was not found; TruthCore will use the configured degraded fallback." >&2
fi

if [[ -n "${SVLS_SOURCE}" ]]; then
  cp "${SVLS_SOURCE}" "${STAGE_BIN}/svls"
  chmod +x "${STAGE_BIN}/svls"
  echo "Bundled svls: ${SVLS_SOURCE}"
  "${STAGE_BIN}/svls" --version | head -1 || true
else
  echo "WARNING: svls was not found; live SystemVerilog lint will be unavailable." >&2
fi

cat > "${STAGE_ROOT}/runtime-manifest.json" <<EOF
{
  "product": "chipverify-desktop",
  "platform": "linux",
  "linux_flavor": "redhat",
  "target_glibc_max": "${REDHAT_GLIBC_MAX}",
  "backend_runtime": "runtime/backend/chipverify-backend",
  "slang_runtime": "runtime/bin/slang",
  "slang_bundled": $([[ -n "${SLANG_SOURCE}" ]] && echo true || echo false),
  "svls_runtime": "runtime/bin/svls",
  "svls_bundled": $([[ -n "${SVLS_SOURCE}" ]] && echo true || echo false),
  "activation_required": true,
  "target": "${TARGET}",
  "backend_source": "${BACKEND_DIST}"
}
EOF

echo "[3/5] Building frontend"
npm run build:frontend

echo "[4/5] Building Red Hat AppImage"
if [[ "${TARGET}" == "all" ]]; then
  npx electron-builder --linux AppImage rpm --x64 --publish never \
    --config.linux.artifactName='${productName}-${version}-redhat-${arch}.${ext}'
else
  npx electron-builder --linux "${TARGET}" --x64 --publish never \
    --config.linux.artifactName='${productName}-${version}-redhat-${arch}.${ext}'
fi

echo "[5/5] Collecting Red Hat artifacts"
rm -rf "${DIST_REDhat}"
mkdir -p "${DIST_REDhat}"
find "${REPO_ROOT}/dist-electron" -maxdepth 1 -type f \
  \( -name '*redhat*.AppImage' -o -name '*redhat*.rpm' -o -name 'latest-linux.yml' \) \
  -exec cp -f {} "${DIST_REDhat}/" \;

mapfile -t ARTIFACTS < <(
  find "${DIST_REDhat}" -maxdepth 1 -type f \
    \( -name '*redhat*.AppImage' -o -name '*redhat*.rpm' -o -name 'latest-linux.yml' \) \
    -print | sort
)
if [[ "${#ARTIFACTS[@]}" -eq 0 ]]; then
  echo "No Red Hat Linux installer artifact was produced." >&2
  exit 1
fi

for artifact in "${ARTIFACTS[@]}"; do
  echo "REDHAT_LINUX_ARTIFACT=${artifact}"
done
