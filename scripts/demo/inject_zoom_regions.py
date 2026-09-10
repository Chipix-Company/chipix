#!/usr/bin/env python3
"""
Inject beat-timed zoomRegions into an OpenScreen project JSON.

Uses scripts/demo/out/beat_timing.json marks (wall-clock from choreograph)
relative to cold_open / first timing key, mapped into recording ms.

Does not enable --auto-zoom; intentional zooms only at important beats.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "scripts" / "demo" / "out"
DEFAULT_PROJECT = OUT_DIR / "chipix_sha256_demo.openscreen"
DEFAULT_TIMING = OUT_DIR / "beat_timing.json"

# Important story beats → zoom depth / duration / focus
ZOOM_SPECS = [
    {"mark": "mm_tour", "duration_s": 6.0, "depth": 2.5, "focus": {"cx": 0.55, "cy": 0.42}},
    {"mark": "ide_shown", "duration_s": 5.5, "depth": 3.0, "focus": {"cx": 0.62, "cy": 0.48}},
    {"mark": "plan_approved", "duration_s": 4.0, "depth": 2.2, "focus": {"cx": 0.48, "cy": 0.55}},
    {"mark": "run_fail", "duration_s": 7.0, "depth": 3.0, "focus": {"cx": 0.50, "cy": 0.52}},
    {"mark": "fix_applied", "duration_s": 5.0, "depth": 3.2, "focus": {"cx": 0.52, "cy": 0.50}},
    {"mark": "verdict", "duration_s": 6.0, "depth": 2.6, "focus": {"cx": 0.50, "cy": 0.48}},
]


def main() -> int:
    project_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROJECT
    timing_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TIMING
    if not project_path.is_file():
        print(f"Missing OpenScreen project: {project_path}", file=sys.stderr)
        return 2
    if not timing_path.is_file():
        print(f"Missing timing: {timing_path}", file=sys.stderr)
        return 3

    timing_doc = json.loads(timing_path.read_text(encoding="utf-8"))
    timing = timing_doc.get("timing") or {}
    if not timing:
        print("No timing marks", file=sys.stderr)
        return 4

    # Origin: earliest mark (usually cold_open) ≈ when record started + buffer.
    origin = min(float(v) for v in timing.values())
    # OpenScreen record starts slightly before first beat; pad 1.2s from choreograph.
    record_origin_pad = 0.0

    regions = []
    for spec in ZOOM_SPECS:
        mark = spec["mark"]
        if mark not in timing:
            print(f"skip zoom (no mark): {mark}")
            continue
        start_s = max(0.0, float(timing[mark]) - origin - record_origin_pad)
        end_s = start_s + float(spec["duration_s"])
        regions.append(
            {
                "id": f"zoom_{mark}_{uuid.uuid4().hex[:6]}",
                "startMs": int(start_s * 1000),
                "endMs": int(end_s * 1000),
                "depth": spec["depth"],
                "focus": spec["focus"],
                "focusMode": "manual",
            }
        )
        print(f"zoom {mark}: {start_s:.1f}s–{end_s:.1f}s depth={spec['depth']}")

    data = json.loads(project_path.read_text(encoding="utf-8"))
    # Project schema nests editor settings; support both shapes.
    editor = data.get("editor") if isinstance(data.get("editor"), dict) else data
    editor["zoomRegions"] = regions
    editor["autoZoomEnabled"] = False
    if "editor" in data and isinstance(data["editor"], dict):
        data["editor"] = editor
    else:
        data.update(editor)

    project_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {len(regions)} zoomRegions → {project_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
