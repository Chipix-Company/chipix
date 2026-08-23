from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

from common.paths import outputs_dir
from typing import Any

from services.verilog_parser import parse_verilog

from .toolchain import detect_toolchain_status, get_toolchain_fingerprint

OUTPUTS_DIR = outputs_dir()


def _cache_dir(project_id: str) -> Path:
    path = OUTPUTS_DIR / "eda-cache" / project_id / "netlist"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _temp_dir() -> Path:
    path = OUTPUTS_DIR / "eda-cache" / "_tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _range_from_port(port: dict[str, Any], module_name: str) -> dict[str, Any]:
    return {
        "kind": "port",
        "label": port.get("name", ""),
        "moduleName": module_name,
        "direction": port.get("direction", "unknown"),
    }


def _build_hierarchy(ast_modules: dict[str, dict[str, Any]], top_module: str) -> list[dict[str, Any]]:
    hierarchy = []
    for module_name, module_data in ast_modules.items():
        hierarchy.append(
            {
                "id": f"module:{module_name}",
                "kind": "module",
                "label": module_name,
                "moduleName": module_name,
                "children": [
                    {
                        "id": f"instance:{module_name}:{instance.get('instance_name', 'inst')}",
                        "kind": "instance",
                        "label": instance.get("instance_name", "inst"),
                        "moduleName": module_name,
                        "instanceName": instance.get("instance_name"),
                        "targetModuleName": instance.get("module_type"),
                        "children": [],
                    }
                    for instance in module_data.get("instances", [])
                ]
                + [
                    {
                        "id": f"signal:{module_name}:{port.get('name', 'sig')}",
                        **_range_from_port(port, module_name),
                        "children": [],
                    }
                    for port in module_data.get("ports", [])
                ],
                "isTopModule": module_name == top_module,
            }
        )
    return hierarchy


def _simple_svg(ast_modules: dict[str, dict[str, Any]], top_module: str) -> str:
    module_names = list(ast_modules.keys()) or [top_module]
    width = 960
    row_height = 140
    height = max(220, len(module_names) * row_height + 40)
    fragments = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#07111f"/>',
        '<text x="24" y="32" fill="#dce7f3" font-size="20" font-family="Inter, sans-serif">RTL Workspace Schematic</text>',
    ]
    for index, module_name in enumerate(module_names):
        y = 56 + index * row_height
        module_data = ast_modules.get(module_name, {})
        ports = module_data.get("ports", [])
        instances = module_data.get("instances", [])
        fragments.extend(
            [
                f'<rect x="24" y="{y}" width="260" height="92" rx="16" fill="#10233b" stroke="#6cc6ff" stroke-width="2"/>',
                f'<text x="44" y="{y + 28}" fill="#ffffff" font-size="18" font-family="JetBrains Mono, monospace">{module_name}</text>',
                f'<text x="44" y="{y + 54}" fill="#91b4d5" font-size="12" font-family="Inter, sans-serif">Ports: {len(ports)} | Instances: {len(instances)}</text>',
            ]
        )
        if module_name == top_module:
            fragments.append(
                f'<text x="44" y="{y + 74}" fill="#7bf1a8" font-size="12" font-family="Inter, sans-serif">Top module</text>'
            )
        for inst_index, instance in enumerate(instances[:3]):
            ix = 360 + inst_index * 180
            fragments.extend(
                [
                    f'<rect x="{ix}" y="{y + 12}" width="144" height="68" rx="14" fill="#1a314f" stroke="#f4bf75" stroke-width="1.5"/>',
                    f'<text x="{ix + 12}" y="{y + 34}" fill="#f4f7fb" font-size="13" font-family="JetBrains Mono, monospace">{instance.get("instance_name", "inst")}</text>',
                    f'<text x="{ix + 12}" y="{y + 56}" fill="#e2b36c" font-size="11" font-family="Inter, sans-serif">{instance.get("module_type", "unknown")}</text>',
                    f'<line x1="284" y1="{y + 46}" x2="{ix}" y2="{y + 46}" stroke="#6cc6ff" stroke-width="2"/>',
                ]
            )
    fragments.append("</svg>")
    return "".join(fragments)


def _run_yosys_netlist(
    *,
    source: str,
    filename: str,
    top_module: str,
    working_dir: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    toolchain = detect_toolchain_status()
    tool_messages = []
    yosys = toolchain["yosys"]
    if not yosys["available"]:
        tool_messages.append(
            {
                "tool": "yosys",
                "level": "warning",
                "message": yosys["guidance"],
            }
        )
        return None, tool_messages

    rtl_path = working_dir / filename
    netlist_path = working_dir / "netlist.json"
    script_path = working_dir / "netlist.ys"
    rtl_path.write_text(source, encoding="utf-8")
    script_path.write_text(
        "\n".join(
            [
                f"read_verilog {rtl_path.name}",
                f"hierarchy -top {top_module}",
                "proc",
                "flatten",
                "opt",
                f"write_json {netlist_path.name}",
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [yosys["path"], "-s", str(script_path)],
        cwd=str(working_dir),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.stdout.strip():
        tool_messages.append({"tool": "yosys", "level": "info", "message": completed.stdout.strip()[:4000]})
    if completed.stderr.strip():
        tool_messages.append({"tool": "yosys", "level": "warning", "message": completed.stderr.strip()[:4000]})
    if completed.returncode != 0 or not netlist_path.exists():
        tool_messages.append(
            {
                "tool": "yosys",
                "level": "error",
                "message": "Yosys netlist generation failed.",
            }
        )
        return None, tool_messages

    return json.loads(netlist_path.read_text(encoding="utf-8")), tool_messages


def render_netlist_document(
    *,
    project_id: str,
    filename: str,
    source: str,
    top_module: str | None = None,
) -> dict[str, Any]:
    toolchain_fingerprint = get_toolchain_fingerprint()
    cache_key = hashlib.sha256(
        f"{project_id}|{filename}|{top_module or ''}|{toolchain_fingerprint}|{source}".encode("utf-8")
    ).hexdigest()
    cache_path = _cache_dir(project_id) / f"{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    # Re-parse from source in a workspace-owned scratch directory because
    # system temp directories are not consistently writable under the desktop sandbox.
    temp_path = _temp_dir() / f"netlist-{uuid.uuid4().hex[:12]}"
    temp_path.mkdir(parents=True, exist_ok=True)
    rtl_path = temp_path / filename
    rtl_path.write_text(source, encoding="utf-8")
    ast_modules, ast_metadata = parse_verilog(rtl_path, return_metadata=True)
    resolved_top = (
        top_module
        if top_module and top_module in ast_modules
        else next(iter(ast_modules.keys()), top_module or "top")
    )
    hierarchy = _build_hierarchy(ast_modules, resolved_top)
    yosys_json, tool_messages = _run_yosys_netlist(
        source=source,
        filename=filename,
        top_module=resolved_top,
        working_dir=temp_path,
    )

    document = {
        "top_module": resolved_top,
        "hierarchy": hierarchy,
        "yosys_json": yosys_json,
        "schematic_svg": _simple_svg(ast_modules, resolved_top),
        "tool_messages": tool_messages
        + [
            {
                "tool": "parser",
                "level": "info",
                "message": f"Parser backend: {ast_metadata.get('parser_backend', 'unknown')}",
            }
        ],
        "cache_key": cache_key,
        "parser_backend": ast_metadata.get("parser_backend"),
        "fallback_used": bool(ast_metadata.get("fallback_used")),
    }
    cache_path.write_text(json.dumps(document), encoding="utf-8")
    return document
