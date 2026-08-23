from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from typing import Any


_TOOL_DEFS = {
    "iverilog": {
        "env": "CHIPVERIFY_IVERILOG_BIN",
        "candidates": ["iverilog"],
        "version_args": ["-V"],
        "guidance": "Install Icarus Verilog and ensure iverilog is on PATH.",
    },
    "vvp": {
        "env": "CHIPVERIFY_VVP_BIN",
        "candidates": ["vvp"],
        "version_args": ["-V"],
        "guidance": "Install Icarus Verilog runtime and ensure vvp is on PATH.",
    },
    "verible": {
        "env": "CHIPVERIFY_VERIBLE_BIN",
        "candidates": ["verible-verilog-syntax"],
        "version_args": ["--version"],
        "guidance": "Install Verible and ensure verible-verilog-syntax is on PATH.",
    },
    "slang": {
        "env": "CHIPVERIFY_SLANG_BIN",
        "candidates": ["slang", "slang.exe"],
        "version_args": ["--version"],
        "guidance": "Install slang and ensure slang is on PATH, set CHIPVERIFY_SLANG_BIN, or bundle runtime/bin/slang.exe. TruthCore can run a degraded regex fallback unless CHIPVERIFY_REQUIRE_SLANG=1.",
    },
    "svls": {
        "env": "CHIPVERIFY_SVLS_BIN",
        "candidates": ["svls", "svls.exe"],
        "version_args": ["--version"],
        "guidance": "Install svls via `cargo install svls`, set CHIPVERIFY_SVLS_BIN, or bundle runtime/bin/svls.exe for IDE lint and agent RTL validation.",
    },
    "yosys": {
        "env": "CHIPVERIFY_YOSYS_BIN",
        "candidates": ["yosys"],
        "version_args": ["-V"],
        "guidance": "Install Yosys and ensure yosys is on PATH.",
    },
    "netlistsvg": {
        "env": "CHIPVERIFY_NETLISTSVG_BIN",
        "candidates": ["netlistsvg"],
        "version_args": ["--version"],
        "guidance": "Install netlistsvg globally or set CHIPVERIFY_NETLISTSVG_BIN.",
    },
    "verilator": {
        "env": "CHIPVERIFY_VERILATOR_BIN",
        "candidates": ["verilator"],
        "version_args": ["--version"],
        "guidance": "Install Verilator and ensure verilator is on PATH.",
    },
    "symbiyosys": {
        "env": "CHIPVERIFY_SBY_BIN",
        "candidates": ["sby"],
        "version_args": ["--version"],
        "guidance": "Install SymbiYosys and ensure sby is on PATH.",
    },
    "questa": {
        "env": "CHIPVERIFY_QUESTA_BIN",
        "candidates": ["vsim"],
        "version_args": ["-version"],
        "guidance": "Install/configure Questa and ensure vsim is on PATH.",
    },
    "vcs": {
        "env": "CHIPVERIFY_VCS_BIN",
        "candidates": ["vcs"],
        "version_args": ["-ID"],
        "guidance": "Install/configure Synopsys VCS and ensure vcs is on PATH.",
    },
    "xcelium": {
        "env": "CHIPVERIFY_XCELIUM_BIN",
        "candidates": ["xrun"],
        "version_args": ["-version"],
        "guidance": "Install/configure Cadence Xcelium and ensure xrun is on PATH.",
    },
}


def _run_version_command(executable: str, version_args: list[str]) -> str:
    try:
        completed = subprocess.run(
            [executable, *version_args],
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


def _resolve_executable(tool_name: str, tool_def: dict[str, Any]) -> str | None:
    configured = (os.environ.get(tool_def["env"]) or "").strip()
    if configured:
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        if os.path.exists(configured):
            return configured

    for candidate in tool_def["candidates"]:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    if tool_name == "slang":
        try:
            from services.mental_model.slang_analyzer import SlangStructuralAnalyzer

            return SlangStructuralAnalyzer.discover_slang_binary()
        except Exception:
            return None
    if tool_name == "svls":
        try:
            from services.sv_lint.toolchain import resolve_svls_binary

            return resolve_svls_binary()
        except Exception:
            return None
    return None


@lru_cache(maxsize=1)
def detect_toolchain_status() -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    for tool_name, tool_def in _TOOL_DEFS.items():
        executable = _resolve_executable(tool_name, tool_def)
        available = bool(executable)
        status[tool_name] = {
            "name": tool_name,
            "available": available,
            "path": executable,
            "version": _run_version_command(executable, tool_def["version_args"])
            if available
            else "",
            "guidance": None if available else tool_def["guidance"],
        }
    return status


def refresh_toolchain_status() -> dict[str, dict[str, Any]]:
    detect_toolchain_status.cache_clear()
    return detect_toolchain_status()


def get_toolchain_fingerprint() -> str:
    status = detect_toolchain_status()
    parts = []
    for tool_name in sorted(status.keys()):
        tool = status[tool_name]
        parts.append(
            f"{tool_name}:{int(bool(tool['available']))}:{tool.get('version') or 'unknown'}"
        )
    return "|".join(parts)
