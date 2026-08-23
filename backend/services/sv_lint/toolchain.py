from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


def _runtime_bin_candidates() -> list[Path]:
    backend_root = Path(__file__).resolve().parents[2]
    names = ["svls.exe", "svls"] if os.name == "nt" else ["svls"]
    repo_root = backend_root.parent
    # Packaged builds stage svls into runtime/bin; the Linux dev fetch
    # (fetch_linux_eda_tools.sh) installs into runtime/linux/eda-tools/bin.
    bin_dirs = [
        backend_root / "runtime" / "bin",
        backend_root / "runtime" / "linux" / "eda-tools" / "bin",
        repo_root / "runtime" / "bin",
    ]
    candidates: list[Path] = []
    for bin_dir in bin_dirs:
        for name in names:
            candidates.append(bin_dir / name)
    return candidates


@lru_cache(maxsize=1)
def resolve_svls_binary() -> str | None:
    configured = (os.environ.get("CHIPVERIFY_SVLS_BIN") or "").strip()
    if configured:
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        if os.path.exists(configured):
            return configured

    for candidate in ["svls.exe", "svls"]:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    for candidate in _runtime_bin_candidates():
        if candidate.exists():
            return str(candidate)
    return None


def svls_available() -> bool:
    return resolve_svls_binary() is not None


def svls_version() -> str:
    binary = resolve_svls_binary()
    if not binary:
        return ""
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    output = (completed.stdout or completed.stderr or "").strip()
    if not output:
        return ""
    return output.splitlines()[0][:240]


def refresh_svls_cache() -> None:
    resolve_svls_binary.cache_clear()
