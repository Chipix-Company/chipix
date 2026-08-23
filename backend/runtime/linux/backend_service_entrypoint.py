"""Linux backend service entrypoint used for PyInstaller ELF builds."""

from __future__ import annotations

import importlib
import multiprocessing
import os
import sys
import traceback
from pathlib import Path


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

# PyInstaller traces these when analyzing the entry script (verification routes lazy-import).
import services.verification.formal_gen as _chipverify_packaged_formal_gen  # noqa: F401
import services.verification.test_plan as _chipverify_packaged_test_plan  # noqa: F401
import services.verification.uvm_gen as _chipverify_packaged_uvm_gen  # noqa: F401
import services.verification.uvm_planner as _chipverify_packaged_uvm_planner  # noqa: F401
import services.verification.uvm_validator as _chipverify_packaged_uvm_validator  # noqa: F401
import simulator_plugins.cadence_integration_fixture as _chipverify_packaged_cadence_fixture  # noqa: F401
import simulator_plugins.xcelium_runner as _chipverify_packaged_xcelium_runner  # noqa: F401


def _exe_dir() -> Path:
    return Path(sys.executable).resolve().parent


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", _exe_dir()))
    return Path(__file__).resolve().parent


def _prepare_frozen_runtime() -> None:
    """Ensure PyInstaller onedir can resolve bundled native libraries on Linux."""

    if not getattr(sys, "frozen", False):
        return

    exe_dir = _exe_dir()
    os.chdir(exe_dir)

    library_dirs = [exe_dir / "_internal", exe_dir]
    existing = os.environ.get("LD_LIBRARY_PATH", "").split(":")
    merged = []
    for candidate in library_dirs:
        if candidate.is_dir() and str(candidate) not in existing:
            merged.append(str(candidate))
    if merged:
        os.environ["LD_LIBRARY_PATH"] = ":".join([*merged, *existing]) if existing else ":".join(merged)


def _log_startup_error(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
    log_path = os.getenv("CHIPVERIFY_BACKEND_LOG", "").strip()
    if not log_path:
        log_path = os.path.join(os.getcwd(), "chipverify-backend-startup.log")
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(message)
            if not message.endswith("\n"):
                handle.write("\n")
    except OSError:
        pass


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


def _load_bundled_env() -> None:
    """Load .env files from the PyInstaller bundle before importing main."""

    from dotenv import load_dotenv

    bundle_root = _bundle_root()
    exe_dir = _exe_dir()

    for candidate in (
        bundle_root / ".env.defaults",
        exe_dir / ".env.defaults",
    ):
        if candidate.is_file():
            load_dotenv(candidate, override=False)

    for candidate in (
        bundle_root / ".env",
        exe_dir / ".env",
    ):
        if candidate.is_file():
            load_dotenv(candidate, override=True)

    custom_env = os.getenv("CHIPVERIFY_ENV_FILE", "").strip()
    if custom_env and Path(custom_env).is_file():
        load_dotenv(custom_env, override=True)


def _init_sentry_early() -> None:
    """Initialize Sentry before importing the app so import-time errors report."""
    try:
        from observability.sentry_config import init_sentry

        init_sentry()
    except Exception:
        # Never let observability setup block backend startup.
        pass


def _import_app():
    _ensure_backend_import_path()
    _load_bundled_env()
    _init_sentry_early()
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
    import uvicorn

    multiprocessing.freeze_support()
    _prepare_frozen_runtime()
    _ensure_backend_import_path()

    if "--self-test-graphify" in sys.argv:
        try:
            _run_graphify_self_test()
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            _log_startup_error(f"Graphify packaged-runtime self-test failed.\n{details}")
            raise SystemExit(1) from exc
        return
    if "--self-test-verification" in sys.argv:
        try:
            _run_verification_self_test()
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            _log_startup_error(f"Verification packaged-runtime self-test failed.\n{details}")
            raise SystemExit(1) from exc
        return
    if "--self-test-packaged" in sys.argv:
        try:
            _run_packaged_self_test()
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            _log_startup_error(f"Packaged-runtime self-test failed.\n{details}")
            raise SystemExit(1) from exc
        return

    try:
        app = _import_app()
    except Exception as exc:
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _log_startup_error(
            "ChipVerify backend failed during startup import.\n"
            f"executable={sys.executable}\n"
            f"cwd={os.getcwd()}\n"
            f"ld_library_path={os.environ.get('LD_LIBRARY_PATH', '')}\n"
            f"secret_key_set={bool(os.getenv('CHIPVERIFY_SECRET_KEY'))}\n"
            f"{details}"
        )
        try:
            from observability.sentry_config import capture_exception

            capture_exception(exc, area="backend.startup", frozen=getattr(sys, "frozen", False))
        except Exception:
            pass
        raise SystemExit(1) from exc

    host = os.getenv("CHIPVERIFY_BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _read_port("CHIPVERIFY_BACKEND_PORT", 7348)
    log_level = os.getenv("CHIPVERIFY_UVICORN_LOG_LEVEL", "info").strip() or "info"

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        workers=1,
        proxy_headers=True,
        server_header=False,
    )


if __name__ == "__main__":
    main()
