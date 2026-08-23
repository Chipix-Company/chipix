#!/usr/bin/env bash
# Fast build: copy repo to native Linux FS (/root/chipverify-build) then build there.
set -euo pipefail

TARGET="${1:-AppImage}"
case "${TARGET}" in
  AppImage|all) ;;
  *) echo "Usage: $0 [AppImage|all]" >&2; exit 2 ;;
esac

# ── CRITICAL: Disable WSL Windows interop + lock to Linux-only PATH ───────────
# Without this, 'npm ci' runs Windows npm (via WSL interop) which tries to run
# cmd.exe post-install scripts and fails on UNC paths like \\wsl.localhost\...
# Disabling interop makes WSL use only Linux binaries for this session.
if [[ -w /proc/sys/fs/binfmt_misc/WSLInterop ]]; then
  echo 0 > /proc/sys/fs/binfmt_misc/WSLInterop
  echo ">>> WSL Windows interop disabled for this session."
fi
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export NODE="/usr/bin/node"
export NPM="/usr/bin/npm"
echo ">>> node: $(/usr/bin/node --version)  npm: $(/usr/bin/npm --version)"

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="/root/chipverify-build"

read_source_env_value() {
  local key="$1"
  grep -m1 -E "^[[:space:]]*${key}[[:space:]]*=" "${SRC}/backend/.env" 2>/dev/null \
    | cut -d= -f2- \
    | tr -d '\r' \
    || true
}

export CHIPVERIFY_LLM_PROVIDER="bedrock"
export MODEL_PROVIDER="bedrock"
export CHIPVERIFY_REQUIRE_PACKAGED_LLM_KEY="true"
export CHIPVERIFY_DEMO_BEDROCK_API_KEY="${CHIPVERIFY_DEMO_BEDROCK_API_KEY:-$(read_source_env_value BEDROCK_API_KEY)}"
if [[ -z "${CHIPVERIFY_DEMO_BEDROCK_API_KEY}" ]]; then
  echo "BEDROCK_API_KEY is missing from backend/.env and CHIPVERIFY_DEMO_BEDROCK_API_KEY is not set." >&2
  exit 1
fi

echo "======================================================"
echo " ChipVerify — AlmaLinux-8 Native FS Build"
echo " Source : ${SRC}"
echo " Dest   : ${DEST}"
echo "======================================================"

# ── Step 1: Sync repo to native Linux FS ─────────────────
echo ""
echo ">>> [SYNC] Copying repo to native Linux FS..."
if [[ "${DEST}" != "/root/chipverify-build" ]]; then
  echo "Refusing to replace unexpected build destination: ${DEST}" >&2
  exit 1
fi
rm -rf -- "${DEST}"
mkdir -p "${DEST}"

# Use tar to copy with exclusions (no rsync available)
echo "  Running tar copy (excludes node_modules, venvs, large artifacts)..."
cd "${SRC}"
tar --ignore-failed-read --exclude='.git' \
    --exclude='node_modules' \
    --exclude='frontend/node_modules' \
    --exclude='.packaging/pyinstaller-linux-venv' \
    --exclude='.packaging/pyinstaller-venv' \
    --exclude='.packaging/pyinstaller-alma8-venv' \
    --exclude='.packaging/pyinstaller-alma8-py39-venv' \
    --exclude='.packaging/pyinstaller-redhat-venv' \
    --exclude='.packaging/backend-smoke-test' \
    --exclude='.packaging/backend-smoke-config-api' \
    --exclude='.packaging/linux-smoke' \
    --exclude='dist-electron' \
    --exclude='dist-linux' \
    --exclude='dist-redhat' \
    --exclude='backend/runtime/linux/build' \
    --exclude='backend/runtime/redhat' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.tmp-*.log' \
    --exclude='parser.out' \
    --exclude='parsetab.py' \
    --exclude='.pytest_cache' \
    --exclude='.pytest_tmp' \
    --exclude='.pytest_tmp_*' \
    --exclude='pytest-cache-files-*' \
    --exclude='tmpon2x5g2b' \
    --exclude='*.db' \
    --exclude='*.db-shm' \
    --exclude='*.db-wal' \
    --exclude='.merge-backups' \
    --exclude='qa-screenshots' \
    --exclude='dist-shareable' \
    --exclude='.tmp-ubuntu-24.04.4-wsl-amd64.wsl' \
    -cf - . | tar -xf - -C "${DEST}"

echo ">>> [SYNC] Done. Repo synced to ${DEST}"

# ── Step 2: Fix CRLF ──────────────────────────────────────
echo ""
echo ">>> [CRLF] Normalizing shell scripts..."
find "${DEST}" -type f -name '*.sh' -exec sed -i 's/\r$//' {} +
chmod +x "${DEST}/scripts/packaging/"*.sh
chmod +x "${DEST}/backend/runtime/linux/"*.sh 2>/dev/null || true
echo ">>> [CRLF] Done."

# ── Step 3: Install npm deps (need node_modules on native FS) ──
echo ""
echo ">>> [NPM] Installing root node_modules on native FS..."
cd "${DEST}"
npm ci

# ── Step 4: Run the Red Hat build ─────────────────────────
echo ""
echo ">>> [BUILD] Starting package_embedded_redhat_linux.sh ${TARGET} ..."
export CHIPVERIFY_BOOTSTRAP_PYTHON=python3.11
export CHIPVERIFY_PYTHON_BIN=python3.11

if [[ "${TARGET}" == "all" ]]; then
  # electron-builder's bundled fpm fails on AlmaLinux 8. Keep linux-unpacked,
  # then assemble the RPM with native rpmbuild below.
  bash ./scripts/packaging/package_embedded_redhat_linux.sh all || true
else
  bash ./scripts/packaging/package_embedded_redhat_linux.sh AppImage
fi

# ── Step 4b: Native RPM build (bypasses broken fpm) ───────
echo ""
if [[ "${TARGET}" == "all" ]]; then
  echo ">>> [RPM] Building RPM with native rpmbuild ..."
  bash ./scripts/packaging/build_rpm_from_unpacked.sh
fi

# ── Step 5: Copy artifacts back to Windows FS ────────────
echo ""
echo ">>> [COPY BACK] Copying artifacts to Windows dist-redhat/ ..."
mkdir -p "${SRC}/dist-redhat"
# AppImage and RPM land in dist-electron after electron-builder + our RPM script
cp -v "${DEST}/dist-electron/"*.AppImage "${SRC}/dist-redhat/" 2>/dev/null || true
if [[ "${TARGET}" == "all" ]]; then
  cp -v "${DEST}/dist-electron/"*.rpm "${SRC}/dist-redhat/" 2>/dev/null || true
fi

echo ""
echo "======================================================"
echo " BUILD COMPLETE — Artifacts in dist-redhat/:"
ls -lh "${SRC}/dist-redhat/" 2>/dev/null || echo "(check ${DEST}/dist-electron/)"
echo "======================================================"
