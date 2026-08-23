from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SLANG_GUIDANCE = (
    "Slang is the preferred TruthCore RTL parser. Install the slang CLI and "
    "ensure it is on PATH, set CHIPVERIFY_SLANG_BIN to slang.exe, or bundle "
    "slang.exe under runtime/bin in the desktop resources. If regex fallback "
    "is enabled, TruthCore will continue in degraded mode."
)


@dataclass
class SlangAnalysisResult:
    module_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    symbol_table: dict[str, list[str]] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    parser_engine: str = "slang"
    parser_version: str = ""


class SlangAnalysisError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostics: list[dict[str, Any]] | None = None,
        missing_tool: bool = False,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or []
        self.missing_tool = missing_tool


class SlangStructuralAnalyzer:
    """Project-level SystemVerilog structural analysis using the slang CLI."""

    def __init__(
        self,
        slang_bin: str | None = None,
        *,
        timeout_seconds: int = 90,
    ) -> None:
        self.slang_bin = slang_bin or self.resolve_slang_binary()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def resolve_slang_binary() -> str:
        for candidate in _candidate_slang_binaries():
            if candidate:
                return candidate
        raise SlangAnalysisError(SLANG_GUIDANCE, missing_tool=True)

    @staticmethod
    def discover_slang_binary() -> str | None:
        for candidate in _candidate_slang_binaries():
            if candidate:
                return candidate
        return None

    def _ensure_slang_available(self) -> None:
        if not self.slang_bin:
            raise SlangAnalysisError(SLANG_GUIDANCE, missing_tool=True)

    def version(self) -> str:
        self._ensure_slang_available()
        try:
            completed = subprocess.run(
                [self.slang_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return ""
        return (completed.stdout or completed.stderr or "").strip().splitlines()[0][:240]

    def analyze_project(
        self,
        root_path: str,
        rtl_files: list[str],
        *,
        include_dirs: list[str] | None = None,
        defines: dict[str, str] | None = None,
        top_module: str | None = None,
    ) -> SlangAnalysisResult:
        self._ensure_slang_available()
        root = Path(root_path).resolve()
        if not root.exists():
            raise FileNotFoundError(f"RTL root path does not exist: {root_path}")

        files = [_resolve_rtl_file(root, path) for path in rtl_files]
        files = [path for path in files if path.exists()]
        if not files:
            raise SlangAnalysisError("No RTL files were found for Slang analysis.")

        include_set = {root}
        for path in files:
            include_set.add(path.parent)
        for raw in include_dirs or []:
            include_set.add((root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve())

        with tempfile.TemporaryDirectory(prefix="chipverify_slang_") as tmp:
            tmp_path = Path(tmp)
            ast_path = tmp_path / "ast.json"
            diag_path = tmp_path / "diagnostics.json"
            cmd_path = tmp_path / "slang_args.f"

            args: list[str] = [
                "--std",
                "1800-2023",
                "--compat",
                "all",
                "--ast-json",
                str(ast_path),
                "--ast-json-source-info",
                "--diag-json",
                str(diag_path),
                "--error-limit",
                "0",
            ]
            if top_module:
                args.extend(["--top", top_module])
            for directory in sorted(include_set, key=lambda item: str(item).lower()):
                args.extend(["--include-directory", str(directory)])
            for name, value in sorted((defines or {}).items()):
                args.extend(["--define-macro", f"{name}={value}"])
            args.extend(str(path) for path in files)

            cmd_path.write_text(
                "\n".join(_quote_command_file_arg(arg) for arg in args),
                encoding="utf-8",
            )
            try:
                completed = subprocess.run(
                    [self.slang_bin, "-F", str(cmd_path)],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise SlangAnalysisError(SLANG_GUIDANCE, missing_tool=True) from exc
            except subprocess.TimeoutExpired as exc:
                raise SlangAnalysisError(
                    f"Slang RTL parse timed out after {self.timeout_seconds} seconds.",
                ) from exc

            diagnostics = _load_diagnostics(diag_path, completed.stderr)
            if completed.returncode != 0 or _has_error_diagnostics(diagnostics):
                raise SlangAnalysisError(
                    _format_slang_error(completed.returncode, diagnostics, completed.stderr),
                    diagnostics=diagnostics,
                )
            if not ast_path.exists():
                raise SlangAnalysisError(
                    "Slang completed without producing AST JSON. TruthCore cannot build a model.",
                    diagnostics=diagnostics,
                )

            try:
                ast = json.loads(ast_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SlangAnalysisError(
                    f"Slang AST JSON could not be parsed: {exc}",
                    diagnostics=diagnostics,
                ) from exc

        module_index = _normalize_slang_ast(ast, root)
        _attach_file_metadata(module_index, root, files)
        symbol_table = _build_symbol_table(module_index)
        return SlangAnalysisResult(
            module_index=module_index,
            symbol_table=symbol_table,
            diagnostics=diagnostics,
            parser_version=self.version(),
        )


def _candidate_slang_binaries() -> list[str]:
    """Return existing slang executables from env, PATH, and packaged resources."""
    candidates: list[str] = []

    configured = (os.environ.get("CHIPVERIFY_SLANG_BIN") or "").strip()
    if configured:
        resolved = _resolve_executable(configured)
        if resolved:
            candidates.append(resolved)

    for name in ("slang", "slang.exe"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    for root in _resource_roots():
        candidates.extend(
            str(path)
            for path in (
                root / "runtime" / "bin" / _slang_executable_name(),
                root / "runtime" / "slang" / _slang_executable_name(),
                # Linux dev fetch (fetch_linux_eda_tools.sh) installs here.
                root / "runtime" / "linux" / "eda-tools" / "bin" / _slang_executable_name(),
                root / "eda-tools" / "bin" / _slang_executable_name(),
                root / "bin" / _slang_executable_name(),
                root / "slang" / _slang_executable_name(),
                root / _slang_executable_name(),
            )
            if path.exists()
        )

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        normalized = str(Path(candidate).resolve())
        if normalized.lower() not in seen:
            seen.add(normalized.lower())
            unique.append(normalized)
    return unique


def _resolve_executable(value: str) -> str | None:
    path = Path(value)
    if path.exists():
        return str(path.resolve())
    return shutil.which(value)


def _slang_executable_name() -> str:
    return "slang.exe" if os.name == "nt" else "slang"


def _resource_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("CHIPVERIFY_RESOURCES_DIR", "CHIPVERIFY_RESOURCE_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            roots.append(Path(raw))

    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(Path(str(meipass)))

    executable_parent = Path(sys.executable).resolve().parent
    roots.extend(
        [
            executable_parent,
            executable_parent.parent,
            executable_parent / "resources",
        ]
    )

    repo_root = Path(__file__).resolve().parents[3]
    roots.extend(
        [
            repo_root,
            repo_root / "backend",
            repo_root / "backend" / "runtime",
            repo_root / "build-resources",
            repo_root / "build-resources" / "runtime",
            repo_root / "tools",
        ]
    )

    result: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def _resolve_rtl_file(root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _quote_command_file_arg(value: str) -> str:
    text = str(value).replace("\\", "/")
    if not text or re.search(r"\s|\"", text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _load_diagnostics(path: Path, stderr: str) -> list[dict[str, Any]]:
    raw: Any = None
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = None
    if raw is None and stderr.strip():
        return [{"severity": "error", "message": line.strip()} for line in stderr.splitlines() if line.strip()]
    if isinstance(raw, list):
        return [_normalize_diagnostic(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        items = raw.get("diagnostics") or raw.get("messages") or raw.get("errors")
        if isinstance(items, list):
            return [_normalize_diagnostic(item) for item in items if isinstance(item, dict)]
        return [_normalize_diagnostic(raw)]
    return []


def _normalize_diagnostic(item: dict[str, Any]) -> dict[str, Any]:
    location = item.get("location") or item.get("sourceLocation") or item.get("file") or ""
    file_name, line, column = _parse_location(location)
    return {
        "severity": str(item.get("severity") or item.get("level") or item.get("kind") or "").lower(),
        "message": str(item.get("message") or item.get("text") or item.get("description") or ""),
        "code": str(item.get("code") or item.get("option") or ""),
        "file": str(item.get("file") or file_name or ""),
        "line": int(item.get("line") or line or 0),
        "column": int(item.get("column") or column or 0),
    }


def _has_error_diagnostics(diagnostics: list[dict[str, Any]]) -> bool:
    return any(str(item.get("severity", "")).lower() in {"error", "fatal"} for item in diagnostics)


def _format_slang_error(returncode: int, diagnostics: list[dict[str, Any]], stderr: str) -> str:
    if diagnostics:
        first = diagnostics[0]
        loc = ""
        if first.get("file"):
            loc = str(first["file"])
            if first.get("line"):
                loc += f":{first['line']}"
            loc += ": "
        return f"Slang RTL parse failed: {loc}{first.get('message') or 'unknown diagnostic'}"
    detail = (stderr or "").strip().splitlines()
    suffix = f": {detail[0]}" if detail else f" with exit code {returncode}"
    return f"Slang RTL parse failed{suffix}"


def _normalize_slang_ast(ast: dict[str, Any], root: Path) -> dict[str, dict[str, Any]]:
    module_index: dict[str, dict[str, Any]] = {}
    design = ast.get("design") if isinstance(ast, dict) else {}

    def visit_instance(node: dict[str, Any], parent_module: str = "") -> None:
        body = node.get("body") if isinstance(node.get("body"), dict) else {}
        module_name = str(body.get("name") or node.get("definition") or node.get("name") or "").strip()
        if not module_name:
            return
        if module_name not in module_index:
            module_index[module_name] = _module_from_instance_body(module_name, body, node, root)
        if parent_module and parent_module in module_index:
            inst_name = str(node.get("name") or module_name)
            if inst_name:
                module_index[parent_module].setdefault("instantiations", []).append(
                    {
                        "module": module_name,
                        "instance": inst_name,
                        "connections": _normalize_connections(node.get("connections", [])),
                    }
                )
        for member in _members(body):
            if isinstance(member, dict) and member.get("kind") == "Instance":
                visit_instance(member, module_name)

    for member in _members(design):
        if isinstance(member, dict) and member.get("kind") == "Instance":
            visit_instance(member)

    if not module_index:
        for instance in _find_instance_nodes(ast):
            visit_instance(instance)

    for definition in ast.get("definitions", []) if isinstance(ast, dict) else []:
        if not isinstance(definition, dict):
            continue
        definition_kind = str(definition.get("definitionKind") or definition.get("kind") or "")
        if definition_kind not in {"Module", "Interface", "Package", "Definition"}:
            continue
        name = str(definition.get("name") or "").strip()
        if name and name not in module_index:
            module_index[name] = _module_from_definition(name, definition, root)

    return module_index


def _module_from_instance_body(
    module_name: str,
    body: dict[str, Any],
    instance: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    ports: list[dict[str, Any]] = []
    parameters: list[dict[str, Any]] = []
    internal_symbols: list[str] = []
    modports: list[dict[str, Any]] = []

    for member in _body_symbols(body):
        if not isinstance(member, dict):
            continue
        kind = str(member.get("kind") or "")
        name = str(member.get("name") or "").strip()
        if not name:
            continue
        if kind == "Port":
            source_file, line, _column = _source_info(member, root)
            port_type = str(member.get("type") or "logic")
            ports.append(
                {
                    "name": name,
                    "direction": _normalize_direction(member.get("direction")),
                    "width": _width_from_type(port_type),
                    "bus_range": _bus_range_from_type(port_type),
                    "port_type": _port_type_from_type(port_type),
                    "line": line,
                    "source_file": source_file,
                }
            )
        elif "Parameter" in kind or kind in {"Parameter", "LocalParam"}:
            parameters.append(
                {
                    "name": name,
                    "default_value": _stringify_value(member.get("value") or member.get("initializer") or member.get("constant")),
                }
            )
        elif kind in {"Variable", "Net", "FormalArgument"}:
            internal_symbols.append(name)
        elif "Modport" in kind:
            modports.append(
                {
                    "name": name,
                    "ports": [
                        str(item.get("name") or item.get("port") or "")
                        for item in _coerce_list(member.get("ports") or member.get("members"))
                        if isinstance(item, dict)
                    ],
                }
            )

    source_file, start_line, _column = _source_info(body or instance, root)
    return {
        "name": module_name,
        "definition_kind": str(
            body.get("definitionKind")
            or instance.get("definitionKind")
            or instance.get("kind")
            or "Module"
        ),
        "file": source_file,
        "line_range": (start_line or 0, start_line or 0),
        "line_count": 0,
        "ports": ports,
        "parameters": parameters,
        "instantiations": [],
        "always_blocks": [],
        "fsm_candidates": [],
        "register_fields": [],
        "existing_assertions": [],
        "cdc_crossings": [],
        "internal_symbols": sorted(set(internal_symbols)),
        "modports": modports,
        "parser_engine": "slang",
    }


def _module_from_definition(name: str, definition: dict[str, Any], root: Path) -> dict[str, Any]:
    body = definition.get("body") if isinstance(definition.get("body"), dict) else definition
    module = _module_from_instance_body(name, body, definition, root)
    module["definition_kind"] = str(definition.get("definitionKind") or definition.get("kind") or "Definition")
    return module


def _members(node: Any) -> list[Any]:
    if isinstance(node, dict):
        members = node.get("members")
        return members if isinstance(members, list) else []
    return []


def _body_symbols(node: Any) -> list[Any]:
    if not isinstance(node, dict):
        return []
    symbols: list[Any] = []
    for key in ("members", "ports", "parameters"):
        values = node.get(key)
        if isinstance(values, list):
            symbols.extend(values)
    return symbols


def _coerce_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _find_instance_nodes(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("kind") == "Instance":
            found.append(node)
        for value in node.values():
            found.extend(_find_instance_nodes(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_find_instance_nodes(value))
    return found


def _normalize_connections(connections: Any) -> list[dict[str, str]]:
    if not isinstance(connections, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in connections:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "port": str(item.get("port") or item.get("name") or ""),
                "arg": _stringify_value(item.get("arg") or item.get("expression") or item.get("value")),
            }
        )
    return normalized


def _normalize_direction(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"in", "input"}:
        return "input"
    if text in {"out", "output"}:
        return "output"
    if text in {"inout", "ref"}:
        return "inout"
    return "input"


def _port_type_from_type(type_text: str) -> str:
    text = str(type_text or "").strip()
    return text.split("[", 1)[0].strip() or "logic"


def _bus_range_from_type(type_text: str) -> str:
    match = re.search(r"\[(\d+)\s*:\s*(\d+)\]", str(type_text or ""))
    return f"[{match.group(1)}:{match.group(2)}]" if match else ""


def _width_from_type(type_text: str) -> int:
    match = re.search(r"\[(\d+)\s*:\s*(\d+)\]", str(type_text or ""))
    if not match:
        return 1
    return abs(int(match.group(1)) - int(match.group(2))) + 1


def _stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("value", "constant", "literal", "name"):
            if key in value:
                return _stringify_value(value[key])
    return str(value)


def _source_info(node: dict[str, Any], root: Path) -> tuple[str, int, int]:
    for key in ("location", "sourceLocation", "sourceRange", "syntax", "file"):
        if key in node:
            file_name, line, column = _parse_location(node[key])
            if file_name or line:
                return _relativize_source(file_name, root), line, column
    for value in node.values():
        if isinstance(value, dict):
            file_name, line, column = _source_info(value, root)
            if file_name or line:
                return file_name, line, column
    return "", 0, 0


def _parse_location(value: Any) -> tuple[str, int, int]:
    if isinstance(value, dict):
        file_name = str(value.get("file") or value.get("filename") or value.get("path") or "")
        line = int(value.get("line") or value.get("startLine") or 0)
        column = int(value.get("column") or value.get("startColumn") or 0)
        return file_name, line, column
    text = str(value or "")
    match = re.search(r"(.+?):(\d+):(\d+)", text)
    if match:
        return match.group(1), int(match.group(2)), int(match.group(3))
    return "", 0, 0


def _relativize_source(file_name: str, root: Path) -> str:
    if not file_name:
        return ""
    try:
        path = Path(file_name)
        if path.is_absolute():
            return str(path.resolve().relative_to(root))
    except Exception:
        pass
    return file_name


def _attach_file_metadata(module_index: dict[str, dict[str, Any]], root: Path, files: list[Path]) -> None:
    if not module_index:
        return
    single_rel = ""
    if len(files) == 1:
        try:
            single_rel = str(files[0].resolve().relative_to(root))
        except Exception:
            single_rel = str(files[0])
    for module in module_index.values():
        if not module.get("file") and single_rel:
            module["file"] = single_rel


def _build_symbol_table(module_index: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    symbol_table: dict[str, list[str]] = {}
    for module_name, module in module_index.items():
        symbols: list[str] = []
        symbols.extend(str(item.get("name", "")) for item in module.get("ports", []) if isinstance(item, dict))
        symbols.extend(str(item.get("name", "")) for item in module.get("parameters", []) if isinstance(item, dict))
        symbols.extend(str(item.get("name", "")) for item in module.get("modports", []) if isinstance(item, dict))
        symbols.extend(str(item) for item in module.get("internal_symbols", []) if item)
        symbols.extend(str(item.get("name", "")) for item in module.get("register_fields", []) if isinstance(item, dict))
        symbol_table[module_name] = sorted({symbol for symbol in symbols if symbol})
    return symbol_table
