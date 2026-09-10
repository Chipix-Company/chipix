#!/usr/bin/env python3
"""Generate a light upbeat SaaS-style instrumental bed (no vocals)."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "scripts" / "demo" / "assets" / "saas_upbeat_bed.wav"


def env_exp(t: float, period: float, decay: float) -> float:
    return math.exp(-decay * (t % period))


def main() -> int:
    sr = 44100
    dur = 185.0
    n = int(sr * dur)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    frames = bytearray()
    for i in range(n):
        t = i / sr
        kick = 0.26 * math.sin(2 * math.pi * 55 * t) * env_exp(t, 0.5, 14)
        hat = (
            0.04
            * (1.0 if int(t * 8) % 2 == 1 else 0.15)
            * math.sin(2 * math.pi * 9000 * t)
            * env_exp(t * 2, 0.125, 55)
        )
        bar = int(t / 2) % 4
        roots = [261.63, 220.00, 174.61, 196.00]
        r = roots[bar]
        pad = 0.055 * (
            math.sin(2 * math.pi * r * t)
            + 0.7 * math.sin(2 * math.pi * r * 5 / 4 * t)
            + 0.55 * math.sin(2 * math.pi * r * 3 / 2 * t)
            + 0.35 * math.sin(2 * math.pi * r * 2 * t)
        ) * (0.65 + 0.35 * math.sin(2 * math.pi * t / 8))
        step = int(t * 4) % 8
        intervals = [1, 5 / 4, 3 / 2, 2, 3 / 2, 5 / 4, 1, 2]
        f = r * intervals[step]
        arp = 0.07 * math.sin(2 * math.pi * f * t) * env_exp(t, 0.25, 9)
        bass = 0.11 * math.sin(2 * math.pi * (r / 2) * t) * (
            0.5 + 0.5 * math.sin(2 * math.pi * t * 2)
        )
        s = kick + hat + pad + arp + bass
        s = max(-0.95, min(0.95, s * 0.85))
        if t < 1.2:
            s *= t / 1.2
        if t > dur - 5:
            s *= max(0.0, (dur - t) / 5)
        sample = int(s * 28000)
        frames += struct.pack("<hh", sample, sample)

    with wave.open(str(OUT), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(frames)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
