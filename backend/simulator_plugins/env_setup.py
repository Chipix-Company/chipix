"""Source a user-provided Cadence environment script and import the result.

University / lab Cadence installs are not normal OS packages: they live under a
large install tree (often on NFS) and only work after a site setup script is
sourced (e.g. ``source cshrc_kle`` in tcsh). That script sets ``PATH``,
``LD_LIBRARY_PATH`` and the license variables (``CDS_LIC_FILE`` /
``LM_LICENSE_FILE``).

ChipVerify's backend is launched from a desktop / Electron session that never
sourced that script, so ``xrun`` and the license are invisible to detection and
Cadence shows up as "off". Setting ``CHIPVERIFY_CADENCE_SETUP`` to the script
path lets the backend source it once, in the correct shell, and import the
resulting Cadence environment into ``os.environ`` before tool detection runs.

This is intentionally conservative: it imports ``PATH`` / ``LD_LIBRARY_PATH``
(which the sourced shell already merged on top of the inherited values) plus any
EDA / license variable and any brand-new variable the script defines. It never
overwrites the backend's own Python runtime variables.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from functools import lru_cache

logger = logging.getLogger(__name__)

SETUP_ENV_VAR = "CHIPVERIFY_CADENCE_SETUP"
SHELL_ENV_VAR = "CHIPVERIFY_CADENCE_SETUP_SHELL"

SETUP_TIMEOUT_S = 30

# Variables that are clearly EDA / license related and safe to import wholesale.
_EDA_PREFIXES = (
    "CDS",
    "CADENCE",
    "XCELIUM",
    "XRUN",
    "LM_LICENSE",
    "LM_PROJECT",
    "UVM",
    "OA_",
    "OPENACCESS",
    "MMSIM",
)

# Path-like variables we merge (the sourced shell already prepended Cadence
# entries on top of the inherited value, so taking the captured value is safe).
_PATH_VARS = ("PATH", "LD_LIBRARY_PATH", "MANPATH")

# Backend-owned runtime variables we must never let a setup script clobber.
_PROTECTED = {
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUNBUFFERED",
    "VIRTUAL_ENV",
    "_",
    "PWD",
    "OLDPWD",
    "SHLVL",
    "PS1",
    "PROMPT",
    "SHELL",
    "TERM",
}


def _is_eda_var(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in _EDA_PREFIXES)


def _shell_command(script: str) -> list[str] | None:
    """Build a command that sources ``script`` and null-delimits the resulting env.

    csh/tcsh setup scripts (the common university case, e.g. ``cshrc_kle``) use
    ``setenv`` syntax that a POSIX shell cannot parse, so we pick the shell from
    an explicit override, the script extension/name, then fall back to sh.
    """
    forced = (os.environ.get(SHELL_ENV_VAR) or "").strip()
    name = os.path.basename(script).lower()
    use_csh = forced in {"csh", "tcsh"} or (
        not forced and ("csh" in name or name.endswith(".csh"))
    )

    # ``env -0`` (GNU coreutils, present on the Linux hosts Cadence targets)
    # null-delimits values so newlines inside a value don't corrupt parsing.
    dump = "env -0"
    if use_csh:
        shell = shutil.which("tcsh") or shutil.which("csh")
        if not shell:
            logger.warning(
                "%s points at a csh-style script but neither tcsh nor csh is "
                "installed; skipping Cadence env import.",
                SETUP_ENV_VAR,
            )
            return None
        return [shell, "-c", f"source '{script}'; {dump}"]

    shell = (forced if forced else "") or shutil.which("bash") or shutil.which("sh") or "sh"
    return [shell, "-c", f". '{script}'; {dump}"]


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_env_dump(raw: str) -> dict[str, str]:
    captured: dict[str, str] = {}
    # Prefer null-delimited records; fall back to newline splitting if the
    # platform `env` ignored `-0`.
    records = raw.split("\0") if "\0" in raw else raw.splitlines()
    for record in records:
        if not record or "=" not in record:
            continue
        key, value = record.split("=", 1)
        # Real-world lab scripts (e.g. cshrc_kle) end with `clear` + echo banners
        # that print to stdout BEFORE `env -0`, so the first record arrives as
        # "<banner text>\nREAL_KEY=value". Strip any leading banner/escape noise
        # by taking the text after the last newline, then accept only records
        # whose key is a valid shell identifier — this drops the garbage without
        # losing the real variable that trails it (PATH, XCELIUMHOME, ...).
        key = key.rsplit("\n", 1)[-1].strip()
        if _ENV_KEY_RE.match(key):
            captured[key] = value
    return captured


def _apply_captured_env(captured: dict[str, str]) -> list[str]:
    applied: list[str] = []
    for key, value in captured.items():
        if key in _PROTECTED:
            continue
        current = os.environ.get(key)
        if key in _PATH_VARS:
            if value and value != current:
                os.environ[key] = value
                applied.append(key)
            continue
        if _is_eda_var(key):
            if value and value != current:
                os.environ[key] = value
                applied.append(key)
            continue
        # Import brand-new, site-specific variables the script introduced
        # (e.g. an institution's license variable) without clobbering anything
        # the backend already relies on.
        if current is None and value:
            os.environ[key] = value
            applied.append(key)
    return applied


@lru_cache(maxsize=1)
def ensure_cadence_env() -> list[str]:
    """Source ``CHIPVERIFY_CADENCE_SETUP`` once and import its Cadence env.

    Returns the list of environment variable names that were applied (empty when
    no setup script is configured, missing, or it failed). Cached so repeated
    detections don't re-spawn a shell; call :func:`reset_cadence_env_cache` after
    changing the configuration to force a re-source.
    """
    script = (os.environ.get(SETUP_ENV_VAR) or "").strip()
    if not script:
        return []
    # Lab scripts are usually referenced as ``~/cshrc_kle`` / ``$HOME/...``;
    # expand so the file check and the sourced path both resolve.
    script = os.path.expandvars(os.path.expanduser(script))
    if not os.path.isfile(script):
        logger.warning("%s=%s does not exist; skipping.", SETUP_ENV_VAR, script)
        return []

    command = _shell_command(script)
    if command is None:
        return []

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=SETUP_TIMEOUT_S,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to source Cadence setup %s: %s", script, exc)
        return []

    if completed.returncode != 0:
        logger.warning(
            "Cadence setup %s exited %s: %s",
            script,
            completed.returncode,
            (completed.stderr or "").strip()[-500:],
        )
        # Still parse stdout — a non-zero rc from a noisy login script does not
        # necessarily mean the env was not set.

    captured = _parse_env_dump(completed.stdout or "")
    if not captured:
        return []
    applied = _apply_captured_env(captured)
    if applied:
        logger.info(
            "Imported Cadence environment from %s (%d variables).",
            script,
            len(applied),
        )
    return applied


def reset_cadence_env_cache() -> None:
    ensure_cadence_env.cache_clear()
