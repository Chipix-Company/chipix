#!/usr/bin/env bash
# Self-diagnosing launcher for the PyInstaller backend on Linux (RHEL/Alma/Rocky/Ubuntu).
#
# Why this exists: when the frozen backend fails to start on a target machine it is
# almost always a *loader* failure (glibc / libstdc++ / missing .so) that happens
# BEFORE the Python interpreter runs — so the Python-level startup logging in
# backend_service_entrypoint.py never gets a chance to fire, and Electron only sees
# the process exit immediately ("backend disconnected"). This wrapper captures the
# real reason (ldd "not found" lines, glibc/GLIBCXX versions, exit code) into the
# backend log, then execs the real ELF so normal operation is unchanged.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${HERE}/chipverify-backend"
LOG="${CHIPVERIFY_BACKEND_LOG:-${HERE}/backend.log}"

# Make the bundled native libraries resolvable (mirrors the logic in main.js and
# backend_service_entrypoint.py so this works no matter which path spawns it).
export LD_LIBRARY_PATH="${HERE}/_internal:${HERE}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

{
  echo "=================================================================="
  echo "ChipVerify backend preflight"
  echo "exe          : ${BIN}"
  echo "cwd          : $(pwd)"
  echo "LD_LIBRARY_PATH: ${LD_LIBRARY_PATH}"
  if command -v ldd >/dev/null 2>&1; then
    echo "glibc        : $(ldd --version 2>&1 | head -1)"
  fi
  if command -v getconf >/dev/null 2>&1; then
    echo "GNU_LIBC     : $(getconf GNU_LIBC_VERSION 2>/dev/null || true)"
  fi
  for libcxx in /lib64/libstdc++.so.6 /usr/lib64/libstdc++.so.6 /usr/lib/x86_64-linux-gnu/libstdc++.so.6; do
    if [[ -e "${libcxx}" ]] && command -v strings >/dev/null 2>&1; then
      echo "system GLIBCXX max (${libcxx}): $(strings "${libcxx}" 2>/dev/null | grep -o 'GLIBCXX_3\.4\.[0-9]*' | sort -V | tail -1)"
      break
    fi
  done
  if [[ -x "${BIN}" ]] && command -v ldd >/dev/null 2>&1; then
    missing="$(ldd "${BIN}" 2>&1 | grep -i 'not found' || true)"
    if [[ -n "${missing}" ]]; then
      echo "ldd MISSING LIBS:"
      echo "${missing}"
    else
      echo "ldd          : all shared libraries resolved"
    fi
  fi
  echo "=================================================================="
} >> "${LOG}" 2>&1

if [[ ! -x "${BIN}" ]]; then
  echo "ChipVerify backend binary missing or not executable: ${BIN}" >> "${LOG}" 2>&1
  exit 127
fi

# exec so signals (SIGTERM from Electron before-quit) reach the real process and
# stdout/stderr keep flowing to the fd Electron handed us.
exec "${BIN}" "$@"
