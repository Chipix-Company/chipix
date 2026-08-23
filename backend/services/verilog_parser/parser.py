from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Literal, overload

try:
    import pyverilog.vparser.ast as vast
    from pyverilog.ast_code_generator.codegen import ASTCodeGenerator
    from pyverilog.vparser.parser import VerilogParser
except ImportError:  # pragma: no cover - exercised only when pyverilog is unavailable
    vast = None
    ASTCodeGenerator = None
    VerilogParser = None

_PREPROCESSOR_LINE_RE = re.compile(r"^\s*`\w+", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)

_INSTANCE_KEYWORDS = {
    "module",
    "endmodule",
    "function",
    "endfunction",
    "task",
    "endtask",
    "always",
    "always_ff",
    "always_comb",
    "initial",
    "assign",
    "if",
    "else",
    "begin",
    "end",
    "case",
    "for",
    "while",
    "generate",
    "wire",
    "reg",
    "logic",
    "input",
    "output",
    "inout",
    "parameter",
    "localparam",
    "integer",
    "real",
    "time",
}


class ASTExtractor:
    """Traverse pyverilog AST nodes and collect structural RTL data."""

    def __init__(self) -> None:
        self.modules: dict[str, dict[str, Any]] = {}
        self._module_stack: list[dict[str, Any]] = []
        self._codegen = ASTCodeGenerator() if ASTCodeGenerator else None

    def visit(self, node: Any) -> None:
        if node is None:
            return
        method = "visit_" + node.__class__.__name__
        visitor = getattr(self, method, self.generic_visit)
        visitor(node)

    def generic_visit(self, node: Any) -> None:
        children_fn = getattr(node, "children", None)
        if not callable(children_fn):
            return
        for child in node.children():
            self.visit(child)

    def visit_ModuleDef(self, node: vast.ModuleDef) -> None:
        module_data: dict[str, Any] = {
            "name": node.name,
            "ports": [],
            "inputs": [],
            "outputs": [],
            "inouts": [],
            "nets": [],
            "wires": [],
            "instances": [],
            "assigns": [],
            "_port_index": {},
            "_net_index": {},
        }
        self.modules[node.name] = module_data
        self._module_stack.append(module_data)

        # Collect undeclared ports early so the graph remains connected.
        portlist = getattr(node, "portlist", None)
        if portlist and getattr(portlist, "ports", None):
            for port in portlist.ports:
                port_name = getattr(port, "name", None)
                if port_name:
                    self._record_port(port_name, "unknown", "")

        for item in getattr(node, "items", []) or []:
            self.visit(item)

        self._module_stack.pop()

    def visit_Decl(self, node: vast.Decl) -> None:
        for decl in getattr(node, "list", []) or []:
            self.visit(decl)

    def visit_Input(self, node: vast.Input) -> None:
        self._record_port(node.name, "input", self._get_width(node))

    def visit_Output(self, node: vast.Output) -> None:
        self._record_port(node.name, "output", self._get_width(node))

    def visit_Inout(self, node: vast.Inout) -> None:
        self._record_port(node.name, "inout", self._get_width(node))

    def visit_Wire(self, node: vast.Wire) -> None:
        self._record_net(node.name, "wire", self._get_width(node))

    def visit_Reg(self, node: vast.Reg) -> None:
        self._record_net(node.name, "reg", self._get_width(node))

    def visit_Integer(self, node: vast.Integer) -> None:
        self._record_net(node.name, "integer", self._get_width(node))

    def visit_Tri(self, node: vast.Tri) -> None:
        self._record_net(node.name, "tri", self._get_width(node))

    def visit_Assign(self, node: vast.Assign) -> None:
        module = self._current_module()
        if module is None:
            return

        lhs_node = getattr(node.left, "var", node.left)
        rhs_node = getattr(node.right, "var", node.right)
        lhs = self._expr_to_str(lhs_node)
        rhs = self._expr_to_str(rhs_node)
        if lhs or rhs:
            module["assigns"].append({"lhs": lhs, "rhs": rhs})

    def visit_InstanceList(self, node: vast.InstanceList) -> None:
        module = self._current_module()
        if module is None:
            return

        module_type = getattr(node, "module", "")
        for index, instance in enumerate(getattr(node, "instances", []) or []):
            instance_name = getattr(instance, "name", None) or f"inst_{index}"
            inst_data = {
                "instance_name": instance_name,
                "module_type": module_type,
                "connections": [],
            }

            for conn_index, portarg in enumerate(
                getattr(instance, "portlist", []) or []
            ):
                port_name = getattr(portarg, "portname", None) or f"${conn_index}"
                arg_value = self._expr_to_str(getattr(portarg, "argname", None))
                inst_data["connections"].append({"port": port_name, "arg": arg_value})

            module["instances"].append(inst_data)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        modules: dict[str, dict[str, Any]] = {}
        for module_name, data in self.modules.items():
            ports = [
                {
                    "name": port["name"],
                    "direction": port["direction"],
                    "width": port.get("width", ""),
                }
                for port in data["ports"]
            ]
            nets = [
                {
                    "name": net["name"],
                    "type": net["type"],
                    "width": net.get("width", ""),
                }
                for net in data["nets"]
            ]
            modules[module_name] = {
                "name": module_name,
                "ports": ports,
                "inputs": [p for p in ports if p["direction"] == "input"],
                "outputs": [p for p in ports if p["direction"] == "output"],
                "inouts": [p for p in ports if p["direction"] == "inout"],
                "nets": nets,
                "wires": list(nets),
                "instances": list(data["instances"]),
                "assigns": list(data["assigns"]),
            }
        return modules

    def _current_module(self) -> dict[str, Any] | None:
        if not self._module_stack:
            return None
        return self._module_stack[-1]

    def _record_port(self, name: str, direction: str, width: str) -> None:
        module = self._current_module()
        if module is None or not name:
            return

        port_index: dict[str, dict[str, str]] = module["_port_index"]
        existing = port_index.get(name)
        if existing is None:
            port_data = {"name": name, "direction": direction, "width": width}
            module["ports"].append(port_data)
            port_index[name] = port_data
            return

        if existing.get("direction") == "unknown" and direction != "unknown":
            existing["direction"] = direction
        if width and not existing.get("width"):
            existing["width"] = width

    def _record_net(self, name: str, net_type: str, width: str) -> None:
        module = self._current_module()
        if module is None or not name:
            return

        net_index: dict[str, dict[str, str]] = module["_net_index"]
        existing = net_index.get(name)
        if existing is None:
            net_data = {"name": name, "type": net_type, "width": width}
            module["nets"].append(net_data)
            net_index[name] = net_data
            return

        if width and not existing.get("width"):
            existing["width"] = width

    def _get_width(self, node: Any) -> str:
        width = getattr(node, "width", None)
        if width is None:
            return ""

        msb = self._expr_to_str(getattr(width, "msb", None))
        lsb = self._expr_to_str(getattr(width, "lsb", None))
        if msb and lsb:
            return f"[{msb}:{lsb}]"
        return ""

    def _expr_to_str(self, node: Any) -> str:
        if node is None:
            return ""
        if isinstance(node, str):
            return node

        if self._codegen is not None:
            try:
                rendered = self._codegen.visit(node)
                if rendered is not None:
                    return " ".join(str(rendered).split())
            except Exception:
                pass

        if vast is not None and isinstance(node, vast.Identifier):
            return node.name
        if vast is not None and isinstance(node, vast.IntConst):
            return str(node.value)
        if hasattr(node, "name"):
            return str(node.name)
        if hasattr(node, "value"):
            return str(node.value)
        return node.__class__.__name__


def _remove_comments(code: str) -> str:
    code = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", code)


def _sanitize_systemverilog_source(code: str) -> str:
    """Normalize common SystemVerilog tokens into pyverilog-friendly syntax."""
    code = _PREPROCESSOR_LINE_RE.sub("", code)
    code = re.sub(r"\balways_ff\b", "always", code)
    code = re.sub(r"\balways_comb\b", "always @(*)", code)
    code = re.sub(r"\balways_latch\b", "always @(*)", code)
    code = re.sub(r"\blogic\b", "wire", code)
    code = re.sub(r"\bbit\b", "wire", code)
    code = re.sub(r"\b(unique|priority)\s+(if|case)\b", r"\2", code)
    return code


def _try_parse_ast(code: str) -> tuple[Any | None, str | None]:
    if VerilogParser is None:
        return None, "pyverilog is not installed"

    parser = VerilogParser()
    try:
        parsed = parser.parse(code)
        if isinstance(parsed, tuple):
            return parsed[0], None
        return parsed, None
    except (
        Exception
    ) as exc:  # pragma: no cover - parser exception details are library-defined
        return None, str(exc)


def _extract_ports_from_header(header: str) -> list[dict[str, str]]:
    ports: list[dict[str, str]] = []
    pattern = re.compile(
        r"(input|output|inout)\s+"
        r"(?:(?:wire|reg|logic|integer)\s+)?"
        r"(?:(\[[^\]]+\])\s+)?"
        r"(\w+)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(header):
        ports.append(
            {
                "name": match.group(3),
                "direction": match.group(1).lower(),
                "width": match.group(2) or "",
            }
        )
    return ports


def _split_decl_names(raw_names: str) -> list[str]:
    names: list[str] = []
    for part in raw_names.split(","):
        name = part.split("=")[0].strip()
        if re.match(r"^\w+$", name):
            names.append(name)
    return names


def _fallback_extract_modules(code: str) -> dict[str, dict[str, Any]]:
    """Best-effort extraction used if AST parsing fails."""
    sanitized = _sanitize_systemverilog_source(_remove_comments(code))
    modules: dict[str, dict[str, Any]] = {}

    module_pattern = re.compile(
        r"\bmodule\s+(\w+)\s*(?:#\s*\(.*?\))?\s*(?:\((.*?)\))?\s*;(.*?)\bendmodule\b",
        re.DOTALL | re.IGNORECASE,
    )

    for module_match in module_pattern.finditer(sanitized):
        module_name = module_match.group(1)
        header = module_match.group(2) or ""
        body = module_match.group(3) or ""

        ports = _extract_ports_from_header(header)
        port_names = {port["name"] for port in ports}

        decl_port_pattern = re.compile(
            r"^\s*(input|output|inout)\s+"
            r"(?:(?:wire|reg|logic|integer)\s+)?"
            r"(?:(\[[^\]]+\])\s*)?"
            r"([^;]+);",
            re.MULTILINE | re.IGNORECASE,
        )
        for match in decl_port_pattern.finditer(body):
            direction = match.group(1).lower()
            width = match.group(2) or ""
            for name in _split_decl_names(match.group(3)):
                if name not in port_names:
                    ports.append(
                        {
                            "name": name,
                            "direction": direction,
                            "width": width,
                        }
                    )
                    port_names.add(name)

        nets: list[dict[str, str]] = []
        net_names: set[str] = set()
        net_pattern = re.compile(
            r"^\s*(wire|reg|logic|tri|integer)\s*(?:(\[[^\]]+\])\s*)?([^;]+);",
            re.MULTILINE | re.IGNORECASE,
        )
        for match in net_pattern.finditer(body):
            net_type = match.group(1).lower()
            width = match.group(2) or ""
            for name in _split_decl_names(match.group(3)):
                if name in port_names or name in net_names:
                    continue
                nets.append({"name": name, "type": net_type, "width": width})
                net_names.add(name)

        assigns: list[dict[str, str]] = []
        assign_pattern = re.compile(r"\bassign\s+(.+?)\s*=\s*(.+?);", re.DOTALL)
        for match in assign_pattern.finditer(body):
            lhs = " ".join(match.group(1).split())
            rhs = " ".join(match.group(2).split())
            assigns.append({"lhs": lhs, "rhs": rhs})

        instances: list[dict[str, Any]] = []
        instance_pattern = re.compile(
            r"^\s*(\w+)\s+(?:#\s*\(.*?\)\s*)?(\w+)\s*\((.*?)\)\s*;",
            re.MULTILINE | re.DOTALL,
        )
        for inst_match in instance_pattern.finditer(body):
            module_type = inst_match.group(1)
            if module_type.lower() in _INSTANCE_KEYWORDS:
                continue

            instance_name = inst_match.group(2)
            conn_block = inst_match.group(3)
            connections: list[dict[str, str]] = []

            named_connections = list(
                re.finditer(r"\.(\w+)\s*\(\s*(.*?)\s*\)", conn_block, re.DOTALL)
            )
            if named_connections:
                for conn_match in named_connections:
                    connections.append(
                        {
                            "port": conn_match.group(1),
                            "arg": " ".join(conn_match.group(2).split()),
                        }
                    )
            else:
                positional = [
                    part.strip() for part in conn_block.split(",") if part.strip()
                ]
                for index, arg in enumerate(positional):
                    connections.append(
                        {"port": f"${index}", "arg": " ".join(arg.split())}
                    )

            instances.append(
                {
                    "instance_name": instance_name,
                    "module_type": module_type,
                    "connections": connections,
                }
            )

        modules[module_name] = {
            "name": module_name,
            "ports": ports,
            "inputs": [p for p in ports if p["direction"] == "input"],
            "outputs": [p for p in ports if p["direction"] == "output"],
            "inouts": [p for p in ports if p["direction"] == "inout"],
            "nets": nets,
            "wires": list(nets),
            "instances": instances,
            "assigns": assigns,
        }

    return modules


@overload
def parse_verilog(
    file_path: str | Path,
    *,
    return_metadata: Literal[False] = False,
) -> dict[str, dict[str, Any]]: ...


@overload
def parse_verilog(
    file_path: str | Path,
    *,
    return_metadata: Literal[True],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]: ...


def parse_verilog(
    file_path: str | Path,
    *,
    return_metadata: bool = False,
) -> dict[str, dict[str, Any]] | tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Parse Verilog/SystemVerilog and return module-level structural data."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Verilog file not found: {path}")

    code = path.read_text(encoding="utf-8")
    metadata: dict[str, Any] = {
        "parser_backend": "pyverilog",
        "sanitized": False,
        "fallback_used": False,
        "errors": [],
    }

    ast, error = _try_parse_ast(code)
    if ast is None:
        if error:
            metadata["errors"].append(error)
        metadata["sanitized"] = True
        ast, error = _try_parse_ast(_sanitize_systemverilog_source(code))
        if ast is None and error:
            metadata["errors"].append(error)

    if ast is not None:
        extractor = ASTExtractor()
        extractor.visit(ast)
        modules = extractor.to_dict()
    else:
        modules = _fallback_extract_modules(code)
        metadata["parser_backend"] = "regex_fallback"
        metadata["fallback_used"] = True

    if return_metadata:
        return modules, metadata
    return modules


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Parse Verilog AST into JSON")
    arg_parser.add_argument("file", help="Path to .v/.sv file")
    arg_parser.add_argument(
        "--metadata",
        action="store_true",
        help="Include parser metadata in output",
    )
    args = arg_parser.parse_args()

    result = parse_verilog(args.file, return_metadata=args.metadata)
    print(json.dumps(result, indent=2))
