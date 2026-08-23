"""Windows backend service entrypoint used for PyInstaller EXE builds."""

from __future__ import annotations

import os
import importlib
import sys
from pathlib import Path

import uvicorn


_GRAPHIFY_SELF_TEST_MODULES = (
    "graphify.extract",
    "graphify.build",
    "graphify.cluster",
    "graphify.analyze",
    "graphify.report",
    "graphify.export",
    "graphify.detect",
    "graphify.affected",
    "graphify.serve",
)

_VERIFICATION_SELF_TEST_MODULES = (
    "services.verification.uvm_gen",
    "services.verification.test_plan",
    "services.verification.formal_gen",
    "services.verification.uvm_planner",
    "services.verification.uvm_validator",
)

_SIMULATOR_PLUGINS_SELF_TEST_MODULES = (
    "simulator_plugins.cadence_integration_fixture",
    "simulator_plugins.xcelium_runner",
)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ensure_backend_import_path() -> None:
    backend_root = str(_backend_root())
    legacy_core_root = str(_backend_root() / "original_core")
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    if legacy_core_root not in sys.path:
        sys.path.insert(0, legacy_core_root)


_ensure_backend_import_path()

if True:  # noqa: SIM108 — PyInstaller static import graph
    import services.verification.formal_gen as _chipverify_packaged_formal_gen  # noqa: F401
    import services.verification.test_plan as _chipverify_packaged_test_plan  # noqa: F401
    import services.verification.uvm_gen as _chipverify_packaged_uvm_gen  # noqa: F401
    import services.verification.uvm_planner as _chipverify_packaged_uvm_planner  # noqa: F401
    import services.verification.uvm_validator as _chipverify_packaged_uvm_validator  # noqa: F401
    import simulator_plugins.cadence_integration_fixture as _chipverify_packaged_cadence_fixture  # noqa: F401
    import simulator_plugins.xcelium_runner as _chipverify_packaged_xcelium_runner  # noqa: F401


def _install_legacy_import_aliases() -> None:
    """Map old top-level imports to their packaged original_core modules."""

    aliases = {
        "config": "original_core.config",
        "core": "original_core.core",
        "agents": "original_core.agents",
        "parsers": "original_core.parsers",
    }
    for alias, target in aliases.items():
        if alias not in sys.modules:
            sys.modules[alias] = importlib.import_module(target)


def _run_graphify_self_test() -> None:
    for module_name in _GRAPHIFY_SELF_TEST_MODULES:
        importlib.import_module(module_name)
    print("GRAPHIFY_SELF_TEST=ok", flush=True)


def _run_verification_self_test() -> None:
    for module_name in _VERIFICATION_SELF_TEST_MODULES:
        importlib.import_module(module_name)
    print("VERIFICATION_SELF_TEST=ok", flush=True)


def _run_simulator_plugins_self_test() -> None:
    for module_name in _SIMULATOR_PLUGINS_SELF_TEST_MODULES:
        importlib.import_module(module_name)
    print("SIMULATOR_PLUGINS_SELF_TEST=ok", flush=True)


def _run_packaged_self_test() -> None:
    _run_graphify_self_test()
    _run_verification_self_test()
    _run_simulator_plugins_self_test()
    print("PACKAGED_SELF_TEST=ok", flush=True)


def _import_app():
    _ensure_backend_import_path()
    _install_legacy_import_aliases()
    from main import app

    return app


def _read_port(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise SystemExit(
            f"{name} must be an integer port. Received '{raw_value}'."
        ) from exc

    if port < 1 or port > 65535:
        raise SystemExit(f"{name} must be between 1 and 65535. Received '{port}'.")

    return port


def main() -> None:
    _ensure_backend_import_path()
    if "--self-test-graphify" in sys.argv:
        _run_graphify_self_test()
        return
    if "--self-test-verification" in sys.argv:
        _run_verification_self_test()
        return
    if "--self-test-packaged" in sys.argv:
        _run_packaged_self_test()
        return

    app = _import_app()
    host = os.getenv("CHIPVERIFY_BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _read_port("CHIPVERIFY_BACKEND_PORT", 7348)
    log_level = os.getenv("CHIPVERIFY_UVICORN_LOG_LEVEL", "info").strip() or "info"

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        proxy_headers=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()
