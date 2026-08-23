"""Persisted manual override for Cadence Xcelium discovery.

Detection (``XceliumPlugin.detect``) reads a handful of environment variables —
``CHIPVERIFY_XCELIUM_BIN``, ``CHIPVERIFY_CADENCE_SETUP`` (+ its shell), and
``CDS_LIC_FILE``. Those are normally set before the backend launches, which is
useless once the app is already running and Cadence shows up as "off".

This module lets a user point ChipVerify at ``xrun`` / a lab setup script / a
license at runtime: the values are validated, written to a small JSON file, and
applied to ``os.environ`` so the very next detection sees them. Because the file
is re-applied on every detection, the choice also survives a backend restart.

The JSON shape::

    {
      "xrun_bin": "/opt/cadence/xcelium/tools/bin/xrun",
      "setup_script": "/opt/site/cshrc_kle",
      "setup_shell": "tcsh",
      "license": "5280@license.edu"
    }

Empty/missing fields are simply not applied (an explicit empty string clears a
field). Manual values win over whatever the process was launched with, since
configuring here is a deliberate "fix it now" action.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH_ENV = "CHIPVERIFY_CADENCE_CONFIG"
OUTPUTS_DIR_ENV = "CHIPVERIFY_OUTPUTS_DIR"

# Saved field -> environment variable detection already honors.
_FIELD_TO_ENV = {
    "xrun_bin": "CHIPVERIFY_XCELIUM_BIN",
    "setup_script": "CHIPVERIFY_CADENCE_SETUP",
    "setup_shell": "CHIPVERIFY_CADENCE_SETUP_SHELL",
    "license": "CDS_LIC_FILE",
}

_FIELDS = tuple(_FIELD_TO_ENV.keys())

# Launch-time values of the variables we manage, captured once before any
# override is applied. The inherited environment is the DEFAULT: a manual
# override takes precedence while set, and clearing it reverts to this default
# rather than wiping the variable the backend was started with.
_ENV_DEFAULTS = {env: os.environ.get(env) for env in _FIELD_TO_ENV.values()}


class CadenceConfigError(ValueError):
    """Raised when a manual Cadence override fails validation."""


def _config_path() -> Path:
    configured = (os.environ.get(CONFIG_PATH_ENV) or "").strip()
    if configured:
        return Path(configured)
    outputs = (os.environ.get(OUTPUTS_DIR_ENV) or "").strip()
    base = Path(outputs) if outputs else Path(__file__).resolve().parents[2] / "outputs"
    return base / "cadence_config.json"


def load_cadence_config() -> dict[str, str]:
    """Return the persisted override (only known string fields), or ``{}``."""
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Ignoring unreadable Cadence config %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    config: dict[str, str] = {}
    for field in _FIELDS:
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            config[field] = value.strip()
    return config


def _save_cadence_config(config: dict[str, str]) -> Path:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _restore_default(env_name: str) -> None:
    """Revert a managed variable to the value the backend was launched with."""
    default = _ENV_DEFAULTS.get(env_name)
    if default is None:
        os.environ.pop(env_name, None)
    elif os.environ.get(env_name) != default:
        os.environ[env_name] = default


def apply_cadence_config() -> list[str]:
    """Apply the persisted override on top of the inherited environment.

    Additive by design: a manual override is set, but a field with no override
    leaves the environment default untouched. Safe to call on every detection.
    Returns the variable names a manual override set.
    """
    config = load_cadence_config()
    applied: list[str] = []
    for field, env_name in _FIELD_TO_ENV.items():
        manual = config.get(field)
        if manual:
            if os.environ.get(env_name) != manual:
                os.environ[env_name] = manual
            applied.append(env_name)
    return applied


def _expand_path(value: str) -> str:
    """Expand ``~`` and ``$VARS`` so lab paths like ``~/cshrc_kle`` resolve."""
    return os.path.expandvars(os.path.expanduser(value))


def _validate(field: str, value: str) -> None:
    if field == "xrun_bin":
        expanded = _expand_path(value)
        if not (os.path.exists(expanded) or shutil.which(expanded)):
            raise CadenceConfigError(
                f"xrun binary not found: {value!r}. Provide the full path to xrun "
                "or a name resolvable on PATH."
            )
    elif field == "setup_script":
        if not os.path.isfile(_expand_path(value)):
            raise CadenceConfigError(
                f"Cadence setup script not found: {value!r}."
            )
    elif field == "setup_shell":
        allowed = {"csh", "tcsh", "bash", "sh"}
        if value not in allowed:
            raise CadenceConfigError(
                f"Unsupported setup shell {value!r}; expected one of {sorted(allowed)}."
            )
    # ``license`` is a free-form port@host / file path string; no validation.


def set_cadence_config(
    *,
    xrun_bin: str | None = None,
    setup_script: str | None = None,
    setup_shell: str | None = None,
    license: str | None = None,
) -> dict[str, str]:
    """Merge the provided fields into the persisted override and apply them.

    ``None`` leaves a field unchanged; an empty string clears it. Non-empty
    values are validated (paths must exist) before anything is written, so a bad
    path fails fast without corrupting the saved config. Returns the resulting
    config.
    """
    updates = {
        "xrun_bin": xrun_bin,
        "setup_script": setup_script,
        "setup_shell": setup_shell,
        "license": license,
    }
    config = load_cadence_config()
    for field, value in updates.items():
        if value is None:
            continue
        cleaned = value.strip()
        if not cleaned:
            # Explicitly cleared -> drop the override and revert this variable
            # to the launch-time environment default.
            config.pop(field, None)
            _restore_default(_FIELD_TO_ENV[field])
            continue
        _validate(field, cleaned)
        # Persist path fields already expanded so detection / sourcing see an
        # absolute path (a stored "~/cshrc_kle" would not resolve at apply time).
        config[field] = _expand_path(cleaned) if field in ("xrun_bin", "setup_script") else cleaned

    _save_cadence_config(config)
    apply_cadence_config()
    return config


def clear_cadence_config() -> None:
    """Remove the persisted override and revert vars to their launch defaults."""
    path = _config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    for env_name in _FIELD_TO_ENV.values():
        _restore_default(env_name)
