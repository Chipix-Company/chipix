"""Lightweight RTL structure extraction for the bundled graph engine."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable


RTL_SUFFIXES = {".v", ".sv", ".svh", ".vh", ".vhd", ".vhdl"}
_MODULE_RE = re.compile(
    r"\bmodule\s+([A-Za-z_]\w*)\b(?P<body>.*?)\bendmodule\b",
    re.DOTALL | re.IGNORECASE,
)
_PORT_RE = re.compile(
    r"\b(input|output|inout)\b\s*"
    r"(?:(?:wire|logic|reg|signed|unsigned|var)\s+)*"
    r"(?:\[[^\]]+\]\s*)?([A-Za-z_]\w*)",
    re.IGNORECASE,
)
_INSTANCE_RE = re.compile(
    r"(?:^|[;\n])\s*([A-Za-z_]\w*)\s*"
    r"(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?"
    r"([A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)
_INSTANCE_KEYWORDS = {
    "always",
    "always_comb",
    "always_ff",
    "always_latch",
    "assert",
    "assign",
    "case",
    "cover",
    "for",
    "function",
    "generate",
    "if",
    "module",
    "property",
    "task",
    "while",
}


def collect_files(source: str | Path, *, root: str | Path | None = None) -> list[Path]:
    """Return supported RTL files below source in stable order."""

    del root  # Kept for compatibility with the original Graphify API.
    path = Path(source)
    if path.is_file():
        return [path] if path.suffix.lower() in RTL_SUFFIXES else []
    if not path.is_dir():
        return []
    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() in RTL_SUFFIXES
    )


def _source_root(files: list[Path]) -> Path:
    if not files:
        return Path.cwd()
    common = Path(os.path.commonpath([str(path.resolve()) for path in files]))
    return common if common.is_dir() else common.parent


def _source_name(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _module_node(name: str, source_file: str, *, confidence: float = 1.0) -> dict:
    return {
        "id": f"rtl:module:{name.lower()}",
        "label": name,
        "file_type": "code",
        "source_file": source_file,
        "confidence_score": confidence,
    }


def extract(
    files: Iterable[str | Path],
    *,
    cache_root: str | Path | None = None,
    parallel: bool = False,
) -> dict[str, list[dict]]:
    """Extract modules, ports, and instantiation relationships from RTL files."""

    del cache_root, parallel  # API compatibility; extraction is deterministic and local.
    paths = [Path(path) for path in files]
    root = _source_root(paths)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add_node(node: dict) -> None:
        node_id = str(node.get("id") or "")
        if node_id:
            nodes.setdefault(node_id, node)

    def add_edge(source: str, target: str, relation: str, confidence: float) -> None:
        key = (source, target, relation)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "confidence": "EXTRACTED",
                "confidence_score": confidence,
                "weight": 1.0,
            }
        )

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        source_file = _source_name(path, root)
        for module_match in _MODULE_RE.finditer(text):
            module_name = module_match.group(1)
            module_id = f"rtl:module:{module_name.lower()}"
            module_text = module_match.group(0)
            add_node(_module_node(module_name, source_file))

            for direction, port_name in _PORT_RE.findall(module_text):
                port_id = f"rtl:port:{module_name.lower()}:{port_name.lower()}"
                add_node(
                    {
                        "id": port_id,
                        "label": f"{port_name} ({direction.lower()})",
                        "file_type": "concept",
                        "source_file": source_file,
                        "confidence_score": 1.0,
                    }
                )
                add_edge(module_id, port_id, "has_port", 1.0)

            for type_name, instance_name in _INSTANCE_RE.findall(module_text):
                if type_name.lower() in _INSTANCE_KEYWORDS:
                    continue
                target_id = f"rtl:module:{type_name.lower()}"
                add_node(_module_node(type_name, source_file, confidence=0.85))
                add_edge(module_id, target_id, "instantiates", 0.95)
                instance_id = (
                    f"rtl:instance:{module_name.lower()}:{instance_name.lower()}"
                )
                add_node(
                    {
                        "id": instance_id,
                        "label": instance_name,
                        "file_type": "code",
                        "source_file": source_file,
                        "confidence_score": 0.95,
                    }
                )
                add_edge(module_id, instance_id, "contains", 0.95)
                add_edge(instance_id, target_id, "instance_of", 0.95)

    return {"nodes": list(nodes.values()), "edges": edges}
