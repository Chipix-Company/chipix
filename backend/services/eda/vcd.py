from __future__ import annotations

from pathlib import Path
from typing import Any


def parse_vcd_file(vcd_path: str | Path) -> dict[str, Any]:
    path = Path(vcd_path)
    if not path.exists():
        raise FileNotFoundError(f"VCD file not found: {path}")

    id_to_signal: dict[str, str] = {}
    signal_width: dict[str, int] = {}
    transitions: dict[str, list[dict[str, Any]]] = {}
    signal_index: list[str] = []
    timescale = "1ns"
    current_time = 0

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("$timescale"):
            timescale = line.replace("$timescale", "").replace("$end", "").strip() or timescale
            continue

        if line.startswith("$var"):
            parts = line.split()
            if len(parts) >= 5:
                width = int(parts[2]) if parts[2].isdigit() else 1
                symbol = parts[3]
                name = parts[4]
                id_to_signal[symbol] = name
                signal_width[name] = width
                transitions.setdefault(name, [])
                if name not in signal_index:
                    signal_index.append(name)
            continue

        if line.startswith("#"):
            try:
                current_time = int(line[1:])
            except ValueError:
                current_time = current_time
            continue

        if line[0] in {"0", "1", "x", "z"} and len(line) >= 2:
            symbol = line[1:]
            signal_name = id_to_signal.get(symbol)
            if signal_name:
                transitions.setdefault(signal_name, []).append(
                    {"time": current_time, "value": line[0]}
                )
            continue

        if line.startswith("b"):
            try:
                value, symbol = line[1:].split(" ", 1)
            except ValueError:
                continue
            signal_name = id_to_signal.get(symbol.strip())
            if signal_name:
                transitions.setdefault(signal_name, []).append(
                    {"time": current_time, "value": value.strip()}
                )

    return {
        "timescale": timescale,
        "signal_index": signal_index,
        "signals": [
            {
                "name": name,
                "kind": "bus" if signal_width.get(name, 1) > 1 else "wire",
                "width": signal_width.get(name, 1),
                "transitions": transitions.get(name, []),
            }
            for name in signal_index
        ],
    }


def slice_waveform_data(
    waveform: dict[str, Any],
    *,
    signals: list[str] | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
) -> dict[str, Any]:
    requested = set(signals or [])
    sliced_signals = []
    observed_start = None
    observed_end = None

    for signal in waveform.get("signals", []):
        if requested and signal["name"] not in requested:
            continue

        filtered = []
        for transition in signal.get("transitions", []):
            time_value = int(transition.get("time", 0))
            if start_time is not None and time_value < start_time:
                continue
            if end_time is not None and time_value > end_time:
                continue
            filtered.append({"time": time_value, "value": transition.get("value", "x")})
            observed_start = time_value if observed_start is None else min(observed_start, time_value)
            observed_end = time_value if observed_end is None else max(observed_end, time_value)

        sliced_signals.append(
            {
                "name": signal["name"],
                "kind": signal.get("kind", "wire"),
                "width": signal.get("width", 1),
                "transitions": filtered,
            }
        )

    return {
        "timescale": waveform.get("timescale", "1ns"),
        "start_time": observed_start if observed_start is not None else (start_time or 0),
        "end_time": observed_end if observed_end is not None else (end_time or 0),
        "signals": sliced_signals,
    }
