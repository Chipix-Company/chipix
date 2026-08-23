"""Simulator plugin registry for external EDA tool integrations."""

from __future__ import annotations

from simulator_plugins.base import ToolDetectionResult
from simulator_plugins.xcelium import XceliumPlugin


def available_simulator_plugins() -> dict[str, XceliumPlugin]:
    return {"xcelium": XceliumPlugin()}


def detect_simulators(refresh: bool = False) -> dict[str, dict]:
    plugins = available_simulator_plugins()
    return {
        name: (plugin.refresh() if refresh else plugin.detect()).to_dict()
        for name, plugin in plugins.items()
    }


__all__ = [
    "ToolDetectionResult",
    "XceliumPlugin",
    "available_simulator_plugins",
    "detect_simulators",
]
