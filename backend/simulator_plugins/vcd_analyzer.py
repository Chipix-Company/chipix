"""Small VCD waveform reader for targeted signal snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class VCDSignal:
    symbol: str
    name: str
    width: int = 1
    changes: list[tuple[int, str]] = field(default_factory=list)


class WaveformAnalyzer:
    def __init__(self, vcd_path: str | Path):
        self.path = Path(vcd_path)
        self.signals: dict[str, VCDSignal] = {}
        if self.path.exists():
            self._parse()

    def get_signals_at_time(self, time_ns: int, *, limit: int = 200) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for signal in list(self.signals.values())[:limit]:
            snapshot[signal.name] = _value_at(signal.changes, int(time_ns))
        return snapshot

    def get_signal_window(self, signal_name: str, center_ns: int, window_ns: int = 50) -> list[dict[str, Any]]:
        signal = next((sig for sig in self.signals.values() if sig.name == signal_name), None)
        if not signal:
            return []
        start = center_ns - window_ns
        end = center_ns + window_ns
        return [
            {"time_ns": time, "value": value}
            for time, value in signal.changes
            if start <= time <= end
        ]

    def detect_x_z_signals(self, time_ns: int) -> list[str]:
        snapshot = self.get_signals_at_time(time_ns)
        return [name for name, value in snapshot.items() if "x" in value.lower() or "z" in value.lower()]

    def detect_no_toggle(self, signal_name: str, start_ns: int, end_ns: int) -> bool:
        window = self.get_signal_window(signal_name, (start_ns + end_ns) // 2, (end_ns - start_ns) // 2)
        values = {item["value"] for item in window}
        return len(values) <= 1

    def _parse(self) -> None:
        current_time = 0
        scopes: list[str] = []
        for raw in self.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("$scope"):
                parts = line.split()
                if len(parts) >= 3:
                    scopes.append(parts[2])
                continue
            if line.startswith("$upscope"):
                if scopes:
                    scopes.pop()
                continue
            if line.startswith("$var"):
                parts = line.split()
                if len(parts) >= 5:
                    width = _to_int(parts[2], 1)
                    symbol = parts[3]
                    name = ".".join([*scopes, parts[4]]) if scopes else parts[4]
                    self.signals[symbol] = VCDSignal(symbol=symbol, name=name, width=width)
                continue
            if line.startswith("#"):
                current_time = _to_int(line[1:], current_time)
                continue
            if line[0] in "01xzXZ" and len(line) > 1:
                symbol = line[1:]
                signal = self.signals.get(symbol)
                if signal:
                    signal.changes.append((current_time, line[0]))
                continue
            if line[0] in "bB":
                try:
                    value, symbol = line[1:].split(None, 1)
                except ValueError:
                    continue
                signal = self.signals.get(symbol)
                if signal:
                    signal.changes.append((current_time, value))


def _value_at(changes: list[tuple[int, str]], time_ns: int) -> str:
    value = "x"
    for time, next_value in changes:
        if time > time_ns:
            break
        value = next_value
    return value


def _to_int(value: str, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default
