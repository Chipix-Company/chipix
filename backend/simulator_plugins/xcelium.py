"""Cadence Xcelium detection and capability reporting."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from functools import lru_cache

from simulator_plugins.base import ToolDetectionResult
from simulator_plugins.cadence_config import apply_cadence_config
from simulator_plugins.env_setup import SETUP_ENV_VAR, ensure_cadence_env


class XceliumPlugin:
    name = "xcelium"
    env_var = "CHIPVERIFY_XCELIUM_BIN"

    @lru_cache(maxsize=1)
    def detect(self) -> ToolDetectionResult:
        # Apply any persisted manual override first (the user pointing us at
        # xrun / a setup script / a license when auto-detection failed), then
        # source the lab setup script so the PATH / license probes below see
        # what a sourced tcsh would.
        apply_cadence_config()
        setup_applied = ensure_cadence_env()
        configured = (os.environ.get(self.env_var) or "").strip()
        executable = configured if configured and os.path.exists(configured) else ""
        if not executable:
            executable = shutil.which(configured or "xrun") or ""

        platform_supported = platform.system().lower() != "windows"
        license_env = os.environ.get("CDS_LIC_FILE") or os.environ.get("LM_LICENSE_FILE") or ""

        # Two-phase model: `xrun -version` succeeds WITHOUT a license (the banner
        # prints before any checkout), so the binary being present does NOT mean a
        # simulation will run — the license is only checked out at the simulate
        # stage and can still fail late with `xmsim: *F,NOLICN`. We therefore
        # distinguish "binary present" (available) from "license configured"
        # (license_ready) from "expected to actually run" (run_ready).
        binary_present = bool(executable)
        available = bool(executable and platform_supported)
        license_ready = bool(license_env)
        run_ready = bool(available and license_ready)

        if not platform_supported:
            status = "unsupported_platform"
        elif not binary_present:
            status = "not_installed"
        elif not license_ready:
            status = "no_license"
        else:
            status = "ready"

        guidance = ""
        if status == "unsupported_platform":
            guidance = (
                "Cadence Xcelium runs on Linux. Use a Linux host, WSL with Cadence "
                "access, or upload logs manually for repair analysis."
            )
        elif status == "not_installed":
            guidance = (
                "xrun was not found. Cadence is typically launched from a sourced "
                f"lab script (e.g. cshrc_kle) — point {SETUP_ENV_VAR} at that "
                "script so the backend imports its PATH and license, or set "
                "CHIPVERIFY_XCELIUM_BIN to the full path of xrun."
            )
        elif status == "no_license":
            guidance = (
                "xrun was found but no Cadence license is configured "
                "(set CDS_LIC_FILE or LM_LICENSE_FILE to port@host, or source the "
                f"lab setup script via {SETUP_ENV_VAR}). Simulations will fail at "
                "the simulate stage with NOLICN until a license is reachable."
            )

        version = self._run_version(executable) if executable else ""
        uvm_available = self._run_uvm_help(executable) if executable else False

        return ToolDetectionResult(
            name=self.name,
            available=available,
            path=executable,
            version=version,
            license_env=license_env,
            uvm_available=uvm_available,
            platform_supported=platform_supported,
            guidance=guidance,
            details={
                "configured_env": self.env_var if configured else "",
                "has_license_env": bool(license_env),
                "raw_available": binary_present,
                "status": status,
                "license_ready": license_ready,
                "run_ready": run_ready,
                "setup_script": (os.environ.get(SETUP_ENV_VAR) or "").strip(),
                "setup_applied": list(setup_applied),
            },
        )

    def refresh(self) -> ToolDetectionResult:
        # Re-source the lab setup script too, so a freshly-configured
        # CHIPVERIFY_CADENCE_SETUP takes effect without restarting the backend.
        from simulator_plugins.env_setup import reset_cadence_env_cache

        reset_cadence_env_cache()
        self.detect.cache_clear()
        return self.detect()

    def check_syntax(
        self,
        file_path,
        *,
        incdirs: list[str] | None = None,
        sv: bool = True,
        uvm: bool = False,
        timeout: int = 30,
    ) -> dict:
        """Best-effort single-file syntax/parse check via ``xrun -compile``.

        ``-compile`` stops before elaboration, so this catches lexer/parser and
        intra-file semantic errors without building the hierarchy. A file that
        imports packages or uses UVM cannot be fully checked in isolation — pass
        ``incdirs`` and any prerequisite packages, or expect ``NOPBIND``. Failure
        is decided by BOTH the non-zero exit code AND parsed ``*E,`` diagnostics,
        because xrun's wrapper return code is not always reliable.
        """
        import tempfile
        from pathlib import Path

        from services.verification.uvm_log_parser import parse_uvm_logs

        detection = self.detect()
        if not detection.available:
            return {
                "status": "unavailable",
                "available": False,
                "guidance": detection.guidance,
                "diagnostics": [],
            }

        target = Path(file_path)
        if not target.exists():
            return {
                "status": "error",
                "available": True,
                "error": f"File not found: {file_path}",
                "diagnostics": [],
            }

        work_dir = tempfile.mkdtemp(prefix="xrun_syntax_")
        command = [detection.path, "-compile", "-nocopyright"]
        if sv:
            command.append("-sv")
        if uvm:
            command.append("-uvm")
        for inc in incdirs or []:
            command.append(f"+incdir+{inc}")
        command.append(str(target))

        try:
            completed = subprocess.run(
                command,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"status": "error", "available": True, "error": "Syntax check timed out.", "diagnostics": []}
        except Exception as exc:  # pragma: no cover - defensive
            return {"status": "error", "available": True, "error": str(exc), "diagnostics": []}

        output = f"{completed.stdout}\n{completed.stderr}"
        analysis = parse_uvm_logs([output], simulator="xcelium")
        errors = [diag for diag in analysis.diagnostics if diag.severity == "error"]
        passed = completed.returncode == 0 and not errors
        return {
            "status": "passed" if passed else "failed",
            "available": True,
            "returncode": completed.returncode,
            "file": target.name,
            "command": command,
            "diagnostics": [diag.to_dict() for diag in analysis.diagnostics],
            "error_count": len(errors),
            "summary": analysis.summary,
        }

    @staticmethod
    def _run_version(executable: str) -> str:
        try:
            completed = subprocess.run(
                [executable, "-version"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except Exception:
            return ""
        text = (completed.stdout or completed.stderr or "").strip()
        return text.splitlines()[0][:240] if text else ""

    @staticmethod
    def _run_uvm_help(executable: str) -> bool:
        try:
            completed = subprocess.run(
                [executable, "-uvm", "-help"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except Exception:
            return False
        output = f"{completed.stdout}\n{completed.stderr}".lower()
        return completed.returncode == 0 or "uvm" in output
