"""PyInstaller hook: bundle simulator plugins (lazy-imported from routes)."""

hiddenimports = [
    "simulator_plugins.cadence_integration_fixture",
    "simulator_plugins.cadence_config",
    "simulator_plugins.xcelium",
    "simulator_plugins.xcelium_runner",
    "simulator_plugins.env_setup",
    "simulator_plugins.base",
]
