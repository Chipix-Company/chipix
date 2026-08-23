from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

import graphviz
from graphviz.backend import ExecutableNotFound

try:  # pragma: no cover - import path depends on execution mode
    from .parser import parse_verilog
except ImportError:  # pragma: no cover
    from parser import parse_verilog


def _stable_node_id(prefix: str, name: str) -> str:
    safe = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_")
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    if not safe:
        safe = "node"
    return f"{prefix}_{safe}_{digest}"


def _signal_label(name: str, width: str = "") -> str:
    if width:
        return f"{name}\\n{width}"
    return name


def _base_signal_name(signal: str) -> str:
    match = re.match(r"^\s*([a-zA-Z_][\w$]*)", signal)
    if match:
        return match.group(1)
    return signal.strip()


def _normalized_ports(module_data: dict[str, Any]) -> list[dict[str, str]]:
    ports = module_data.get("ports") or []
    if ports:
        return ports

    # Backward-compatible fallback if only grouped fields are present.
    fallback_ports: list[dict[str, str]] = []
    for direction in ("inputs", "outputs", "inouts"):
        dir_name = direction[:-1]
        for port in module_data.get(direction, []) or []:
            fallback_ports.append(
                {
                    "name": port.get("name", ""),
                    "direction": dir_name,
                    "width": port.get("width", ""),
                }
            )
    return fallback_ports


def _normalized_nets(module_data: dict[str, Any]) -> list[dict[str, str]]:
    nets = module_data.get("nets")
    if nets is not None:
        return nets
    return module_data.get("wires", []) or []


def _save_dot(dot: graphviz.Digraph, output_dir: Path, module_name: str) -> str:
    dot_path = output_dir / f"{module_name}.dot"
    dot.save(filename=str(dot_path))
    return str(dot_path)


def build_module_graph(
    module_name: str,
    module_data: dict[str, Any],
    output_dir: str | Path = ".",
    output_format: str = "svg",
) -> str:
    """Render a structural graph for a parsed module and return output file path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_format = (output_format or "svg").lower().strip()

    dot = graphviz.Digraph(name=module_name, comment=f"RTL module {module_name}")
    dot.attr(
        rankdir="LR",
        fontsize="11",
        fontname="Helvetica",
        labelloc="t",
        label=f"Module: {module_name}",
        splines="spline",
    )

    ports = _normalized_ports(module_data)
    nets = _normalized_nets(module_data)
    instances = module_data.get("instances", []) or []
    assigns = module_data.get("assigns", []) or []

    inputs = [port for port in ports if port.get("direction") == "input"]
    outputs = [port for port in ports if port.get("direction") == "output"]
    inouts = [port for port in ports if port.get("direction") == "inout"]

    input_names = {port.get("name", "") for port in inputs}
    output_names = {port.get("name", "") for port in outputs}

    signal_nodes: dict[str, str] = {}

    def ensure_signal_node(name: str, role: str = "net", width: str = "") -> str:
        if name in signal_nodes:
            return signal_nodes[name]

        node_id = _stable_node_id("SIG", name)
        if role == "input":
            dot.node(
                node_id,
                _signal_label(name, width),
                shape="invhouse",
                style="filled",
                fillcolor="#d9e9fb",
                color="#6c9bd2",
            )
        elif role == "output":
            dot.node(
                node_id,
                _signal_label(name, width),
                shape="house",
                style="filled",
                fillcolor="#d9f3d9",
                color="#6cab6c",
            )
        elif role == "inout":
            dot.node(
                node_id,
                _signal_label(name, width),
                shape="diamond",
                style="filled",
                fillcolor="#ece3ff",
                color="#8b72c4",
            )
        else:
            dot.node(
                node_id,
                _signal_label(name, width),
                shape="ellipse",
                style="filled",
                fillcolor="#f3f3f3",
                color="#8a8a8a",
            )

        signal_nodes[name] = node_id
        return node_id

    with dot.subgraph(name=_stable_node_id("cluster_in", module_name)) as in_cluster:
        in_cluster.attr(label="Inputs", style="rounded,dashed", color="#7aa6d6")
        for port in inputs:
            ensure_signal_node(
                port.get("name", ""), role="input", width=port.get("width", "")
            )

    with dot.subgraph(name=_stable_node_id("cluster_out", module_name)) as out_cluster:
        out_cluster.attr(label="Outputs", style="rounded,dashed", color="#7ab67a")
        for port in outputs:
            ensure_signal_node(
                port.get("name", ""), role="output", width=port.get("width", "")
            )

    for port in inouts:
        ensure_signal_node(
            port.get("name", ""), role="inout", width=port.get("width", "")
        )

    for net in nets:
        net_name = net.get("name", "")
        if not net_name:
            continue
        if net_name in signal_nodes:
            continue
        ensure_signal_node(net_name, role="net", width=net.get("width", ""))

    instance_nodes: dict[str, str] = {}
    with dot.subgraph(
        name=_stable_node_id("cluster_inst", module_name)
    ) as inst_cluster:
        inst_cluster.attr(label="Instances", style="rounded", color="#c5a864")
        for index, instance in enumerate(instances):
            instance_name = instance.get("instance_name") or f"inst_{index}"
            module_type = instance.get("module_type") or "unknown"
            instance_id = _stable_node_id("INST", instance_name)
            instance_nodes[instance_name] = instance_id

            inst_cluster.node(
                instance_id,
                f"{instance_name}\\n({module_type})",
                shape="box",
                style="filled",
                fillcolor="#fff1d8",
                color="#c5a864",
            )

    for index, instance in enumerate(instances):
        instance_name = instance.get("instance_name") or f"inst_{index}"
        instance_id = instance_nodes.get(instance_name)
        if not instance_id:
            continue

        for connection in instance.get("connections", []) or []:
            signal = (connection.get("arg") or "").strip()
            if not signal:
                continue

            signal_id = ensure_signal_node(signal)
            signal_base = _base_signal_name(signal)
            port_label = connection.get("port", "")

            if signal_base in input_names:
                dot.edge(signal_id, instance_id, label=port_label)
            elif signal_base in output_names:
                dot.edge(instance_id, signal_id, label=port_label)
            else:
                dot.edge(signal_id, instance_id, label=port_label, dir="none")

    for assign in assigns:
        lhs = (assign.get("lhs") or "").strip()
        rhs = (assign.get("rhs") or "").strip()
        if not lhs or not rhs:
            continue

        lhs_id = ensure_signal_node(lhs)
        rhs_id = ensure_signal_node(rhs)
        dot.edge(rhs_id, lhs_id, label="assign", color="#c75757", style="dashed")

    if output_format == "dot":
        return _save_dot(dot, output_dir, module_name)

    try:
        rendered_path = dot.render(
            filename=module_name,
            directory=str(output_dir),
            format=output_format,
            cleanup=True,
        )
    except ExecutableNotFound:
        # Desktop-safe fallback: keep visualization generation non-blocking.
        return _save_dot(dot, output_dir, module_name)

    return str(Path(rendered_path))


def visualize_verilog_file(
    file_path: str | Path,
    output_dir: str | Path = ".",
    output_format: str = "svg",
) -> dict[str, str]:
    """Parse an RTL file and render one graph per module."""
    modules = parse_verilog(file_path)
    generated_files: dict[str, str] = {}
    for module_name, module_data in modules.items():
        generated_files[module_name] = build_module_graph(
            module_name,
            module_data,
            output_dir=output_dir,
            output_format=output_format,
        )
    return generated_files


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Generate Verilog structural graphs")
    cli.add_argument("file", help="Path to RTL source file (.v/.sv)")
    cli.add_argument(
        "--output-dir",
        default=".",
        help="Directory for generated graph files",
    )
    cli.add_argument(
        "--format",
        default="svg",
        choices=["svg", "png", "pdf", "dot"],
        help="Graph output format",
    )
    args = cli.parse_args()

    results = visualize_verilog_file(
        args.file,
        output_dir=args.output_dir,
        output_format=args.format,
    )
    for module_name, path in results.items():
        print(f"Generated {module_name}: {path}")
