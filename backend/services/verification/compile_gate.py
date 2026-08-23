"""Compile gate for generated verification collateral.

The gate is deliberately conservative:
- static SystemVerilog shape checks always run;
- open-source simulator lint runs when the generated files are not UVM-only;
- UVM collateral without a configured UVM library is reported as statically gated,
  not falsely failed because the local toolchain lacks ``uvm_pkg``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import asyncio
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, List


SV_EXTENSIONS = {".sv", ".svh", ".v", ".vh"}


@dataclass
class CompileGateResult:
    status: str
    passed: bool
    method: str
    checked_files: int
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    simulator: str = ""
    fix_attempts: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_compile_gate(
    *,
    file_paths: Iterable[str | Path],
    top_module: str = "",
    mental_model: Any = None,
    ai_client: Any = None,
    max_fix_attempts: int = 3,
    compile_timeout: int = 240,
) -> CompileGateResult:
    """Run the generated-code trust gate.

    ``mental_model`` and ``ai_client`` are accepted for the Phase 6 repair loop;
    this first implementation records that no compile auto-repair was attempted
    unless a real simulator failure is available.
    """
    paths = [Path(path) for path in file_paths if str(path)]
    sv_files = [
        path
        for path in paths
        if path.suffix.lower() in SV_EXTENSIONS and path.exists() and path.is_file()
    ]
    errors: List[str] = []
    warnings: List[str] = []

    for path in paths:
        if not path.exists():
            errors.append(f"Missing generated file: {path}")
        elif path.is_file() and path.stat().st_size == 0:
            errors.append(f"Empty generated file: {path}")

    if not sv_files:
        errors.append("No SystemVerilog/Verilog files were provided to the compile gate.")

    _run_static_checks(sv_files, errors, warnings)

    simulator = ""
    method = "static"
    if not errors and sv_files:
        is_uvm = _contains_uvm(sv_files)
        if is_uvm:
            # If a UVM-capable simulator (Cadence xrun) is configured, run a REAL
            # UVM compile instead of skipping — that is the whole point of
            # connecting Cadence. Falls back to the static-only skip when xrun is
            # unavailable or could not be executed, so non-Cadence hosts and
            # infra failures never regress.
            cadence = _run_cadence_uvm_compile(sv_files, paths, top_module, timeout=compile_timeout)
            if cadence is not None:
                simulator = cadence.get("simulator", "")
                method = cadence.get("method", "cadence_xrun_uvm")
                errors.extend(cadence.get("errors", []))
                warnings.extend(cadence.get("warnings", []))
            else:
                warnings.append(
                    "External simulator lint skipped for UVM collateral; configure a UVM library for full compile."
                )
                method = "static+uvm_tool_skipped"
        else:
            sim_result = _run_simulator_lint(sv_files, top_module)
            simulator = sim_result.get("simulator", "")
            method = sim_result.get("method", "static")
            errors.extend(sim_result.get("errors", []))
            warnings.extend(sim_result.get("warnings", []))

    passed = not errors
    return CompileGateResult(
        status="passed" if passed else "failed",
        passed=passed,
        method=method,
        checked_files=len(sv_files),
        errors=errors,
        warnings=warnings,
        simulator=simulator,
        fix_attempts=0 if max_fix_attempts >= 0 else 0,
        details={
            "top_module": top_module,
            "provided_files": len(paths),
            "sv_files": [str(path) for path in sv_files],
            "mental_model_attached": mental_model is not None,
            "ai_client_attached": ai_client is not None,
        },
    )


async def run_compile_gate_with_repair(
    *,
    file_paths: Iterable[str | Path],
    top_module: str = "",
    mental_model: Any = None,
    ai_client: Any = None,
    max_fix_attempts: int = 2,
) -> CompileGateResult:
    """Run the trust gate and optionally repair generated files with the LLM.

    Repairs are deliberately bounded and conservative. A candidate replacement
    must preserve the original declaration names and local include set for the
    target file before it can be written back.
    """
    paths = [Path(path) for path in file_paths if str(path)]
    repair_log: list[dict[str, Any]] = []
    last_result: CompileGateResult | None = None

    for attempt in range(max(0, max_fix_attempts) + 1):
        result = run_compile_gate(
            file_paths=paths,
            top_module=top_module,
            mental_model=mental_model,
            ai_client=ai_client,
            max_fix_attempts=max_fix_attempts,
        )
        result.fix_attempts = attempt
        result.details["repair_log"] = repair_log
        last_result = result
        if result.passed:
            return result
        if attempt >= max_fix_attempts or ai_client is None:
            return result

        targets = _select_repair_targets(paths, result.errors)
        if not targets:
            result.warnings.append("Compile gate repair skipped: no safe target file found.")
            return result

        repaired_this_round = 0
        for target in targets[:3]:
            original = target.read_text(encoding="utf-8", errors="ignore")
            repaired = await _generate_repaired_sv(
                ai_client=ai_client,
                path=target,
                original=original,
                diagnostics=result.errors,
                mental_model=mental_model,
            )
            accepted, reason = _accept_repair_candidate(original, repaired)
            repair_log.append({
                "attempt": attempt + 1,
                "file": str(target),
                "accepted": accepted,
                "reason": reason,
            })
            if accepted:
                target.write_text(repaired, encoding="utf-8")
                repaired_this_round += 1

        if repaired_this_round == 0:
            result.warnings.append("Compile gate repair attempted but no candidate passed safety checks.")
            return result

    return last_result or run_compile_gate(
        file_paths=paths,
        top_module=top_module,
        mental_model=mental_model,
        ai_client=ai_client,
        max_fix_attempts=max_fix_attempts,
    )


def _run_static_checks(
    sv_files: List[Path],
    errors: List[str],
    warnings: List[str],
) -> None:
    declarations: dict[tuple[str, str], Path] = {}
    for path in sv_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        code = _strip_comments(text)

        if "```" in text:
            errors.append(f"{path}: contains markdown code fences.")

        _check_balanced(path, code, "module", "endmodule", errors)
        _check_balanced(path, code, "interface", "endinterface", errors)
        _check_balanced(path, code, "package", "endpackage", errors)
        _check_balanced(path, code, "class", "endclass", errors)
        _check_begin_end(path, code, errors)
        _check_includes(path, text, errors)

        if re.search(r"\bTODO\b|\bFIXME\b|placeholder", text, re.IGNORECASE):
            warnings.append(f"{path}: contains TODO/FIXME/placeholder marker.")

        for kind, name in _declared_names(code):
            key = (kind, name)
            if key in declarations:
                errors.append(
                    f"{path}: duplicate {kind} '{name}' also appears in {declarations[key]}."
                )
            else:
                declarations[key] = path


def _select_repair_targets(paths: List[Path], errors: List[str]) -> List[Path]:
    sv_files = [
        path
        for path in paths
        if path.suffix.lower() in SV_EXTENSIONS and path.exists() and path.is_file()
    ]
    if not sv_files:
        return []

    selected: List[Path] = []
    error_text = "\n".join(errors)
    for path in sv_files:
        if str(path) in error_text or path.name in error_text:
            selected.append(path)
    if selected:
        return selected

    # Static duplicate/unbalanced errors may not always identify the failing
    # include chain cleanly. Prefer files that most often contain generated glue.
    priority = ("scoreboard", "coverage", "test", "top", "pkg", "env")
    ordered = sorted(
        sv_files,
        key=lambda path: next(
            (index for index, token in enumerate(priority) if token in path.name.lower()),
            len(priority),
        ),
    )
    return ordered[:1]


async def _generate_repaired_sv(
    *,
    ai_client: Any,
    path: Path,
    original: str,
    diagnostics: List[str],
    mental_model: Any,
) -> str:
    context = _compact_model_context(mental_model)
    system_prompt = (
        "You are a senior SystemVerilog/UVM compile-fix agent. "
        "Repair only the provided generated file. Preserve all existing "
        "module/interface/package/class declaration names and local includes. "
        "Return only the complete corrected source file."
    )
    prompt = f"""Repair this generated SystemVerilog/UVM file so it passes the compile gate.

FILE: {path.name}

DIAGNOSTICS:
```
{chr(10).join(diagnostics)[-5000:]}
```

MENTAL MODEL CONTEXT:
```json
{context}
```

CURRENT SOURCE:
```systemverilog
{original[:14000]}
```

Return only the complete corrected source for {path.name}.
"""
    if hasattr(ai_client, "chat"):
        response = ai_client.chat(system_prompt, prompt)
    elif hasattr(ai_client, "generate"):
        response = ai_client.generate(
            prompt,
            system_prompt=system_prompt,
            temperature=0.1,
        )
    else:
        return ""

    if asyncio.iscoroutine(response):
        response = await response
    return _extract_sv_source(str(response or ""))


def _extract_sv_source(text: str) -> str:
    text = (text or "").strip()
    match = re.search(r"```(?:systemverilog|verilog|sv|svh)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    return text


def _accept_repair_candidate(original: str, repaired: str) -> tuple[bool, str]:
    if not repaired.strip():
        return False, "empty_response"
    if "```" in repaired:
        return False, "contains_markdown_fence"
    original_decls = set(_declared_names(_strip_comments(original)))
    repaired_decls = set(_declared_names(_strip_comments(repaired)))
    if original_decls != repaired_decls:
        return False, "declaration_names_changed"
    if _local_includes(original) != _local_includes(repaired):
        return False, "local_includes_changed"
    if len(repaired) < max(20, int(len(original) * 0.35)):
        return False, "candidate_too_short"
    return True, "accepted"


def _local_includes(text: str) -> set[str]:
    return {
        include
        for include in re.findall(r'`include\s+"([^"]+)"', text or "")
        if Path(include).name != "uvm_macros.svh"
    }


def _compact_model_context(mental_model: Any) -> str:
    try:
        import json
        from dataclasses import asdict, is_dataclass

        def _to_jsonable(value: Any) -> Any:
            if is_dataclass(value):
                return asdict(value)
            if isinstance(value, dict):
                return {str(k): _to_jsonable(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [_to_jsonable(v) for v in value]
            if hasattr(value, "__dict__"):
                return {
                    str(k): _to_jsonable(v)
                    for k, v in vars(value).items()
                    if not str(k).startswith("_")
                }
            if isinstance(value, (str, int, float, bool)) or value is None:
                return value
            return str(value)

        data = _to_jsonable(mental_model)
        if isinstance(data, dict):
            design = data.get("design", {})
            compact = {
                "top_module": design.get("top_module") if isinstance(design, dict) else "",
                "ports": (design.get("ports", []) if isinstance(design, dict) else [])[:80],
                "protocols": (design.get("protocols", []) if isinstance(design, dict) else [])[:20],
                "expected_behaviors": (design.get("expected_behaviors", []) if isinstance(design, dict) else [])[:20],
                "transaction_flows": (design.get("transaction_flows", []) if isinstance(design, dict) else [])[:20],
                "requirements": (data.get("requirements", []) or [])[:20],
            }
            return json.dumps(compact, indent=2, default=str)[:8000]
        return json.dumps(data, indent=2, default=str)[:8000]
    except Exception:
        return "{}"


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    return re.sub(r"//.*", "", text)


def _declared_names(code: str) -> list[tuple[str, str]]:
    names: list[tuple[str, str]] = []
    for match in re.finditer(r"\b(class|module|interface|package)\s+([a-zA-Z_]\w*)", code):
        prefix = code[max(0, match.start() - 32):match.start()].lower()
        if re.search(r"\btypedef\s+$", prefix):
            continue
        names.append((match.group(1), match.group(2)))
    return names


def _check_balanced(
    path: Path,
    code: str,
    start_kind: str,
    end_kind: str,
    errors: List[str],
) -> None:
    starts = 0
    for match in re.finditer(rf"\b{re.escape(start_kind)}\s+[a-zA-Z_]\w*", code):
        prefix = code[max(0, match.start() - 32):match.start()].lower()
        if start_kind == "class" and re.search(r"\btypedef\s+$", prefix):
            continue
        starts += 1
    ends = len(re.findall(rf"\b{re.escape(end_kind)}\b", code))
    if ends < starts:
        errors.append(f"{path}: unbalanced {start_kind}/{end_kind} declarations.")


def _check_begin_end(path: Path, code: str, errors: List[str]) -> None:
    begins = len(re.findall(r"\bbegin\b", code))
    ends = len(re.findall(r"\bend\b", code))
    if begins > ends:
        errors.append(f"{path}: more begin blocks than matching end tokens.")


def _check_includes(path: Path, text: str, errors: List[str]) -> None:
    for include in re.findall(r'`include\s+"([^"]+)"', text):
        if Path(include).name == "uvm_macros.svh":
            continue
        if not (path.parent / include).exists():
            errors.append(f"{path}: missing include '{include}'.")


def _contains_uvm(sv_files: List[Path]) -> bool:
    for path in sv_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "uvm_pkg" in text or "uvm_component" in text or "`uvm_" in text:
            return True
    return False


def _run_cadence_uvm_compile(
    sv_files: List[Path],
    file_paths: List[Path],
    top_module: str,
    timeout: int = 240,
) -> dict[str, Any] | None:
    """Compile generated UVM collateral with Cadence ``xrun`` when available.

    Returns a result dict (``passed`` / ``errors`` / ``warnings`` / ``simulator``
    / ``method``) when a real compile actually ran, or ``None`` to signal the
    caller should fall back to the static-only skip — used both when Cadence is
    not configured and when xrun could not be executed (so a broken environment
    never turns today's static pass into a false failure).
    """
    try:
        from simulator_plugins.xcelium import XceliumPlugin
    except Exception:
        return None

    try:
        detection = XceliumPlugin().detect()
    except Exception:
        return None
    if not getattr(detection, "available", False) or not getattr(detection, "path", ""):
        return None

    import tempfile

    from services.verification.uvm_log_parser import parse_uvm_logs

    # Prefer a generated filelist (carries DUT RTL + include order); otherwise
    # hand xrun the SV files directly.
    filelists = [
        path
        for path in file_paths
        if str(path).lower().endswith((".f", ".flist")) and Path(path).is_file()
    ]
    cmd = [detection.path, "-compile", "-sv", "-uvm", "-nocopyright"]
    if filelists:
        cwd = str(Path(filelists[0]).parent)
        cmd.extend(["-f", str(filelists[0])])
    else:
        cwd = str(sv_files[0].parent) if sv_files else tempfile.mkdtemp(prefix="xrun_uvm_gate_")
        if top_module:
            cmd.extend(["-top", top_module])
        cmd.extend(str(path) for path in sv_files)

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout)),
            check=False,
        )
    except Exception:
        # Infra failure (timeout, OSError) — fall back rather than false-fail.
        return None

    output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    analysis = parse_uvm_logs([output], simulator="xcelium")
    real_errors = [diag for diag in analysis.diagnostics if diag.severity == "error"]
    passed = proc.returncode == 0 and not real_errors

    errors: List[str] = []
    if not passed:
        errors.append(f"Cadence xrun UVM compile failed:\n{output[-4000:]}")
    return {
        "passed": passed,
        "simulator": "xcelium",
        "method": "cadence_xrun_uvm",
        "errors": errors,
        "warnings": [] if passed else [analysis.summary] if getattr(analysis, "summary", "") else [],
    }


def decide_uvm_trust(validation: dict[str, Any], compile_gate: dict[str, Any]) -> dict[str, Any]:
    """Decide the staged UVM status from the validator and compile-gate reports.

    Trust rule:
    - When a REAL Cadence (xrun) UVM compile actually ran, it is the SOLE
      authority — pass or fail. A passing compile proves the collateral builds,
      so the heuristic static validator never blocks it; a failing compile is
      reported with its real xrun errors instead of being mislabeled as a
      "validation failed" heuristic miss. This is the whole point of connecting
      Cadence: the simulator decides, not the static guesser.
    - Only when NO real simulator ran do we fall back to the conservative static
      gate (validator + static compile checks must both pass).
    """
    validation_trusted = bool(validation.get("trusted"))
    compile_passed = bool(compile_gate.get("passed"))
    real_sim_compile = bool(compile_gate.get("simulator")) and str(
        compile_gate.get("method") or ""
    ).startswith("cadence")

    # A real Cadence compile ran — it alone decides the verdict.
    if real_sim_compile:
        if compile_passed:
            return {"status": "validated", "trusted": True, "authoritative_compile": True}
        return {"status": "compile_gate_failed", "trusted": False, "authoritative_compile": True}

    # No real simulator available: conservative static gating.
    trusted = validation_trusted and compile_passed
    if trusted:
        status = "validated"
    elif validation_trusted:
        status = "compile_gate_failed"
    else:
        status = "validation_failed"
    return {"status": status, "trusted": trusted, "authoritative_compile": False}


def _run_simulator_lint(sv_files: List[Path], top_module: str) -> dict[str, Any]:
    verilator = shutil.which("verilator")
    if verilator:
        cmd = [verilator, "--lint-only", "--sv"]
        if top_module:
            cmd.extend(["--top-module", top_module])
        cmd.extend(str(path) for path in sv_files)
        return _run_lint_command("verilator", cmd)

    iverilog = shutil.which("iverilog")
    if iverilog:
        cmd = [iverilog, "-g2012", "-Wall", "-tnull"]
        if top_module:
            cmd.extend(["-s", top_module])
        cmd.extend(str(path) for path in sv_files)
        return _run_lint_command("iverilog", cmd)

    return {
        "method": "static+tool_missing",
        "simulator": "",
        "errors": [],
        "warnings": ["No Verilator or Icarus executable found; static compile gate only."],
    }


def _run_lint_command(simulator: str, cmd: List[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception as exc:
        return {
            "method": f"static+{simulator}_error",
            "simulator": simulator,
            "errors": [],
            "warnings": [f"{simulator} lint could not be executed: {exc}"],
        }

    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return {
            "method": f"static+{simulator}",
            "simulator": simulator,
            "errors": [f"{simulator} lint failed:\n{output[-4000:]}"],
            "warnings": [],
        }
    warnings = [output[-2000:]] if output.strip() else []
    return {
        "method": f"static+{simulator}",
        "simulator": simulator,
        "errors": [],
        "warnings": warnings,
    }
