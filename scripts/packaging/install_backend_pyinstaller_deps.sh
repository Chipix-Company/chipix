#!/usr/bin/env bash
# Install backend + PyInstaller deps for desktop packaging.

install_backend_pyinstaller_deps() {
  local python_bin="${1:?python executable required}"
  local requirements="${2:?requirements.txt path required}"

  "${python_bin}" -m pip install -r "${requirements}" pyinstaller
}
