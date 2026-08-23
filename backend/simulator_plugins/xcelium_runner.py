"""Xcelium compile/elaborate/simulate runner."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from simulator_plugins.base import (
    SimulatorCommandResult,
    SimulatorRunRequest,
    SimulatorRunResult,
)
from simulator_plugins.closure_report import build_closure_report
from simulator_plugins.coverage_reader import merge_coverage_reports, parse_coverage_report
from simulator_plugins.diagnostic_report import (
    build_findings,
    build_integrity,
    build_traceability,
    deterministic_seed,
    write_closure_markdown,
)
from simulator_plugins.log_pipeline import analyze_large_logs
from simulator_plugins.repair_loop import apply_generated_repairs, build_feedback_memory
from simulator_plugins.spec_grounded_classifier import build_evidence_bundles
from simulator_plugins.vcd_analyzer import WaveformAnalyzer
from simulator_plugins.xcelium import XceliumPlugin


def _emit_phase(request: SimulatorRunRequest, phase: str, **extra: object) -> None:
    if not request.emit_events or not request.project_id:
        return
    try:
        from services.cadence_run.events import emit_project_event_sync

        emit_project_event_sync(
            request.project_id,
            {
                "phase": phase,
                "run_id": request.run_id or request.run_dir.name,
                **extra,
            },
        )
    except Exception:
        pass


class XceliumRunner:
    def __init__(self, plugin: XceliumPlugin | None = None) -> None:
        self.plugin = plugin or XceliumPlugin()

    def run(
        self,
        request: SimulatorRunRequest,
        *,
        mental_model: dict | None = None,
        rtl_root: str | Path | None = None,
        progress_callback: Any = None,
    ) -> SimulatorRunResult:
        detection = self.plugin.detect()
        request.run_dir.mkdir(parents=True, exist_ok=True)
        _emit_phase(request, "detect", status="started")
        commands: list[SimulatorCommandResult] = []
        logs: dict[str, str] = {}
        repair_history: list[dict] = []
        warnings: list[str] = []
        executions: list[dict[str, Any]] = []
        compile_passed = False
        elaborate_passed = False
        environment = self._environment_summary(detection)

        tests = self._regression_tests(request)
        self._publish_progress(progress_callback, {
            "type": "regression_manifest",
            "message": f"Regression manifest ready with {len(tests)} test(s)",
            "tests": len(tests),
        })

        if request.dry_run or request.mock_logs:
            logs = self._write_mock_logs(request)
        elif not detection.available:
            warnings.append(detection.guidance or "Xcelium is not available.")
            _emit_phase(request, "detect", status="unavailable", guidance=detection.guidance)
            result = SimulatorRunResult(
                simulator="xcelium",
                run_id=request.run_dir.name,
                status="unavailable",
                run_dir=str(request.run_dir),
                warnings=warnings,
            )
            self._persist_result(request.run_dir, result)
            return result
        else:
            _emit_phase(request, "detect", status="ready", path=detection.path)
            executable = detection.path
            for round_index in range(1, max(1, request.max_repair_rounds + 1) + 1):
                _emit_phase(request, "compile", status="started", round=round_index)
                compile_log = f"compile_round_{round_index}.log"
                compile_command = [
                    executable,
                    "-compile",
                    "-sv",
                    "-uvm",
                    "-f",
                    str(request.filelist),
                    "-logfile",
                    compile_log,
                ]
                self._publish_progress(progress_callback, {
                    "type": "phase_start",
                    "phase": "compile",
                    "round": round_index,
                    "command": compile_command,
                    "message": f"Xcelium compile round {round_index} started",
                })
                compile_result = self._run_command(
                    phase=f"compile_round_{round_index}",
                    command=compile_command,
                    cwd=request.run_dir,
                    timeout=request.timeout_seconds,
                )
                commands.append(compile_result)
                self._publish_progress(progress_callback, {
                    "type": "phase_complete",
                    "phase": "compile",
                    "round": round_index,
                    "returncode": compile_result.returncode,
                    "passed": compile_result.passed,
                    "message": f"Xcelium compile round {round_index} {'passed' if compile_result.passed else 'failed'}",
                })
                logs[f"compile_round_{round_index}"] = str(request.run_dir / compile_log)
                logs["compile"] = str(request.run_dir / compile_log)
                if compile_result.passed:
                    compile_passed = True
                    _emit_phase(request, "compile", status="passed", round=round_index)
                    break
                _emit_phase(
                    request,
                    "compile",
                    status="failed",
                    round=round_index,
                    log_path=compile_result.log_path,
                )
                if not request.auto_repair_generated or round_index > request.max_repair_rounds:
                    break
                compile_analysis = self._analysis_for_log(Path(compile_result.log_path))
                repair_round = apply_generated_repairs(
                    analysis=compile_analysis,
                    run_dir=request.run_dir,
                    round_index=round_index,
                )
                repair_history.append(repair_round.to_dict())
                _emit_phase(
                    request,
                    "repair",
                    status=repair_round.status,
                    round=round_index,
                    repairs=len(repair_round.repairs),
                )
                if repair_round.status != "repaired":
                    break

            if compile_passed:
                (request.run_dir / "compile.log").write_text(
                    Path(logs["compile"]).read_text(encoding="utf-8", errors="ignore"),
                    encoding="utf-8",
                )
                logs["compile"] = str(request.run_dir / "compile.log")
                elaborate_command = [executable, "-elaborate", "-sv", "-uvm", "-top", request.top_module, "-f", str(request.filelist)]
                # Cadence bakes coverage/access into the elaboration snapshot. Passing these
                # only at run time (-R) triggers the NOCOVE "snapshot not instrumented" error,
                # so the instrumentation flags must be present here.
                elaborate_command.extend(self._instrumentation_flags(request))
                elaborate_command.extend(["-logfile", "elaborate.log"])
                _emit_phase(request, "elaborate", status="started")
                self._publish_progress(progress_callback, {
                    "type": "phase_start",
                    "phase": "elaborate",
                    "command": elaborate_command,
                    "message": "Xcelium elaboration started",
                })
                elaborate_result = self._run_command(
                    phase="elaborate",
                    command=elaborate_command,
                    cwd=request.run_dir,
                    timeout=request.timeout_seconds,
                )
                commands.append(elaborate_result)
                self._publish_progress(progress_callback, {
                    "type": "phase_complete",
                    "phase": "elaborate",
                    "returncode": elaborate_result.returncode,
                    "passed": elaborate_result.passed,
                    "message": f"Xcelium elaboration {'passed' if elaborate_result.passed else 'failed'}",
                })
                _emit_phase(
                    request,
                    "elaborate",
                    status="passed" if elaborate_result.passed else "failed",
                    log_path=elaborate_result.log_path,
                )
                logs["elaborate"] = str(request.run_dir / "elaborate.log")
                elaborate_passed = elaborate_result.passed
            if compile_passed and elaborate_passed:
                for test_index, test in enumerate(tests, start=1):
                    execution, command_result = self._simulate_test(
                        executable=executable,
                        request=request,
                        test=test,
                        index=test_index,
                        replay=False,
                        progress_callback=progress_callback,
                    )
                    commands.append(command_result)
                    executions.append(execution)
                    key = f"simulation_{self._slug(str(test.get('id') or test_index))}"
                    logs[key] = execution["log"]
                    logs.setdefault("simulation", execution["log"])
                    if execution["status"] == "failed" and request.replay_failures:
                        self._publish_progress(progress_callback, {
                            "type": "replay_start",
                            "test": execution.get("test_class"),
                            "seed": execution.get("seed"),
                            "message": f"Replaying {execution.get('test_class')} with seed {execution.get('seed')}",
                        })
                        replay, replay_command = self._simulate_test(
                            executable=executable,
                            request=request,
                            test=test,
                            index=test_index,
                            replay=True,
                            progress_callback=progress_callback,
                        )
                        commands.append(replay_command)
                        execution["replay"] = replay
                        executions.append(replay)
                        logs[f"{key}_replay"] = replay["log"]
                        self._publish_progress(progress_callback, {
                            "type": "replay_complete",
                            "test": execution.get("test_class"),
                            "seed": execution.get("seed"),
                            "passed": replay.get("status") == "passed",
                            "message": f"Failure replay {replay.get('status')}",
                        })

        log_texts = []
        active_log_paths = [
            logs[key]
            for key in ("compile", "elaborate", "simulation")
            if key in logs
        ]
        if not active_log_paths:
            active_log_paths = list(logs.values())
        for path in active_log_paths:
            file_path = Path(path)
            if file_path.exists():
                log_texts.append(file_path.read_text(encoding="utf-8", errors="ignore"))
        pipeline = analyze_large_logs(log_texts, simulator="xcelium")
        vcd_path = request.run_dir / "simulation.vcd"
        waveform_snapshot = WaveformAnalyzer(vcd_path).get_signals_at_time(0) if vcd_path.exists() else {}
        evidence = [
            bundle.to_dict()
            for bundle in build_evidence_bundles(
                clusters=pipeline["clusters"],
                mental_model=mental_model,
                rtl_root=rtl_root,
                waveform_snapshot=waveform_snapshot,
            )
        ]
        self._publish_progress(progress_callback, {"type": "coverage_start", "message": "Reading functional and code coverage closure"})
        imc = self._export_imc_coverage(request.run_dir) if request.coverage and not request.dry_run else {"status": "not_run"}
        coverage_paths = self._coverage_report_paths(request.run_dir)
        coverage = (
            merge_coverage_reports(coverage_paths, targets=request.coverage_targets)
            if coverage_paths
            else parse_coverage_report(request.run_dir / "coverage.txt", targets=request.coverage_targets)
        )
        coverage["imc"] = imc
        coverage["raw_database"] = self._coverage_database_paths(request.run_dir)
        self._publish_progress(progress_callback, {
            "type": "coverage_complete",
            "message": f"Coverage closure is {coverage.get('closure', {}).get('status', 'missing')}",
            "coverage": coverage,
        })
        integrity = build_integrity(
            compile_passed=compile_passed or bool(request.dry_run),
            elaborate_passed=elaborate_passed or bool(request.dry_run),
            static_validation=request.static_validation,
            rtl_checksums=request.rtl_checksums,
            repair_history=repair_history,
        )
        primary_executions = [item for item in executions if item.get("kind") != "replay"]
        regression = {
            "status": "failed" if any(item.get("status") == "failed" for item in primary_executions) else "passed",
            "execution": "sequential",
            "replay_failures": request.replay_failures,
            "total": len(primary_executions),
            "passed": sum(1 for item in primary_executions if item.get("status") == "passed"),
            "failed": sum(1 for item in primary_executions if item.get("status") == "failed"),
            "executions": executions,
            "tests": tests,
        }
        self._publish_progress(progress_callback, {"type": "spec_audit_start", "message": "Auditing every captured specification requirement"})
        traceability = build_traceability(
            mental_model=mental_model,
            tests=tests,
            executions=primary_executions,
            coverage=coverage,
            spec_pages=request.spec_pages,
            spec_metadata=request.spec_metadata,
        )
        self._publish_progress(progress_callback, {
            "type": "spec_audit_complete",
            "message": f"Spec audit mapped {traceability.get('requirements_covered', 0)}/{traceability.get('requirements_total', 0)} requirement(s)",
            "traceability": traceability,
        })
        findings = build_findings(
            regression=regression,
            integrity=integrity,
            traceability=traceability,
            evidence=evidence,
        )
        self._publish_progress(progress_callback, {
            "type": "classify_complete",
            "message": f"Classified {len(findings)} failure finding(s)",
            "findings": findings,
        })
        closure = build_closure_report(
            analysis=pipeline["analysis"],
            evidence=evidence,
            coverage=coverage,
            rtl_integrity=integrity.get("rtl"),
        )
        feedback_memory = build_feedback_memory(
            analysis=pipeline["analysis"],
            evidence=evidence,
            repair_history=repair_history,
        )
        closure["feedback_memory"] = feedback_memory
        closure.update({"integrity": integrity, "traceability": traceability, "findings": findings})
        status = "passed" if pipeline["analysis"].get("status") == "passed" and not warnings else "failed"
        if warnings and not log_texts:
            status = "unavailable"
        if request.enforce_closure:
            tiers = {str(item.get("tier")) for item in findings}
            if tiers & {"Confirmed", "Likely"}:
                status = "needs_action"
            elif (
                findings
                or not integrity.get("passed")
                or traceability.get("status") != "complete"
                or coverage.get("closure", {}).get("status") != "closed"
                or regression.get("failed")
            ):
                status = "needs_review"
            else:
                status = "passed"
        report_warnings = list(warnings)
        if imc.get("status") not in {"exported", "not_run"}:
            report_warnings.append(str(imc.get("reason") or imc.get("error") or "IMC coverage enrichment was unavailable."))
        if coverage.get("status") == "missing":
            report_warnings.append("No readable functional/code coverage summary was exported; coverage closure remains open.")
        report = write_closure_markdown(
            run_dir=request.run_dir,
            run_id=request.run_dir.name,
            simulator_version=str(getattr(detection, "version", "") or ""),
            regression=regression,
            integrity=integrity,
            coverage=coverage,
            traceability=traceability,
            findings=findings,
            environment=environment,
            warnings=report_warnings,
            commands=[command.to_dict() for command in commands],
        )
        self._publish_progress(progress_callback, {
            "type": "report_complete",
            "message": f"Professional Markdown report ready: {report.get('filename')}",
            "report": report,
        })
        artifact_paths = list(dict.fromkeys([
            *logs.values(),
            *coverage.get("sources", []),
            *coverage.get("raw_database", []),
            *[item.get("waveform") for item in executions if item.get("waveform")],
            report.get("path"),
        ]))
        result = SimulatorRunResult(
            simulator="xcelium",
            run_id=request.run_dir.name,
            status=status,
            run_dir=str(request.run_dir),
            commands=commands,
            logs=logs,
            analysis=pipeline["analysis"],
            evidence=evidence,
            coverage=coverage,
            closure_report=closure,
            repair_history=repair_history,
            warnings=warnings,
            regression=regression,
            integrity=integrity,
            traceability=traceability,
            findings=findings,
            report=report,
            environment=environment,
            artifact_paths=[str(path) for path in artifact_paths if path],
        )
        self._persist_result(request.run_dir, result)
        _emit_phase(
            request,
            "complete",
            status=status,
            analysis_status=pipeline["analysis"].get("status"),
        )
        return result

    def _simulate_test(
        self,
        *,
        executable: str,
        request: SimulatorRunRequest,
        test: dict[str, Any],
        index: int,
        replay: bool,
        progress_callback: Any,
    ) -> tuple[dict[str, Any], SimulatorCommandResult]:
        test_id = str(test.get("id") or f"test_{index}")
        test_class = str(test.get("test_class") or request.uvm_testname or "")
        seed = int(test.get("seed") or deterministic_seed(request.seed_namespace or request.run_dir.name, test_id))
        slug = self._slug(test_id)
        suffix = "_replay" if replay else ""
        phase = f"simulate_{slug}{suffix}"
        logfile = f"{phase}.log"
        command = [
            executable,
            "-R",
            "-sv",
            "-uvm",
            "-top",
            request.top_module,
            "-f",
            str(request.filelist),
            "-svseed",
            str(seed),
            "-logfile",
            logfile,
        ]
        if test_class:
            command.append(f"+UVM_TESTNAME={test_class}")
        if request.coverage:
            command.extend(["-coverage", "all", "-covoverwrite", "-covtest", f"{slug}{suffix}"])
        self._publish_progress(progress_callback, {
            "type": "phase_start",
            "phase": "simulate",
            "test": test_class or test_id,
            "test_index": index,
            "seed": seed,
            "replay": replay,
            "command": command,
            "message": f"Xcelium {'replay' if replay else 'simulation'} started for {test_class or test_id} (seed {seed})",
        })
        result = self._run_command(
            phase=phase,
            command=command,
            cwd=request.run_dir,
            timeout=request.timeout_seconds,
        )
        analysis = self._analysis_for_log(Path(result.log_path)).to_dict()
        passed = result.passed and analysis.get("status") == "passed"
        waveform = self._capture_waveform(request.run_dir, f"{slug}{suffix}")
        waveform_evidence = self._waveform_evidence(waveform, analysis)
        execution = {
            "kind": "replay" if replay else "primary",
            "test_id": test_id,
            "test_class": test_class,
            "requirement_ids": list(test.get("requirement_ids") or []),
            "checkers": list(test.get("checkers") or []),
            "assertions": list(test.get("assertions") or []),
            "coverpoints": list(test.get("coverpoints") or []),
            "seed": seed,
            "status": "passed" if passed else "failed",
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            "log": result.log_path,
            "waveform": waveform,
            "waveform_evidence": waveform_evidence,
            "analysis": analysis,
        }
        self._publish_progress(progress_callback, {
            "type": "phase_complete",
            "phase": "simulate",
            "test": test_class or test_id,
            "test_index": index,
            "seed": seed,
            "replay": replay,
            "returncode": result.returncode,
            "passed": passed,
            "message": f"{test_class or test_id} {'passed' if passed else 'failed'} with seed {seed}",
        })
        return execution, result

    @staticmethod
    def _regression_tests(request: SimulatorRunRequest) -> list[dict[str, Any]]:
        tests = [dict(item) for item in request.regression_tests if isinstance(item, dict)]
        if tests:
            return tests
        return [{
            "id": "default",
            "name": "default",
            "test_class": request.uvm_testname,
            "requirement_ids": [],
            "checkers": [],
            "assertions": [],
            "coverpoints": [],
        }]

    @staticmethod
    def _capture_waveform(run_dir: Path, slug: str) -> str:
        candidates = [
            path for path in run_dir.rglob("*.vcd")
            if "waveforms" not in path.parts and path.is_file()
        ]
        if not candidates:
            return ""
        source = max(candidates, key=lambda path: path.stat().st_mtime)
        target_dir = run_dir / "waveforms"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{slug}.vcd"
        shutil.copy2(source, target)
        return str(target)

    @staticmethod
    def _waveform_evidence(waveform: str, analysis: dict[str, Any]) -> dict[str, Any]:
        if not waveform:
            return {}
        diagnostics = analysis.get("diagnostics") or []
        first = diagnostics[0] if diagnostics else {}
        text = f"{first.get('raw') or ''} {first.get('message') or ''}"
        match = re.search(r"@\s*(\d+)", text)
        failure_time = int(match.group(1)) if match else 0
        analyzer = WaveformAnalyzer(waveform)
        signals = analyzer.get_signals_at_time(failure_time, limit=80)
        return {
            "failure_time": failure_time,
            "signals": signals,
            "xz_signals": [name for name, value in signals.items() if "x" in value.lower() or "z" in value.lower()],
        }

    @staticmethod
    def _coverage_report_paths(run_dir: Path) -> list[str]:
        results = []
        for path in run_dir.rglob("*"):
            if not path.is_file():
                continue
            lower = path.name.lower()
            if (
                ("coverage" in lower or lower.startswith("imc"))
                and path.suffix.lower() in {".txt", ".rpt", ".csv"}
                and not lower.endswith(".log")
            ):
                results.append(str(path))
        return list(dict.fromkeys(results))

    @staticmethod
    def _coverage_database_paths(run_dir: Path) -> list[str]:
        paths = [path for path in run_dir.rglob("*.ucd") if path.is_file()]
        cov_work = run_dir / "cov_work"
        if cov_work.exists() and cov_work.is_dir():
            archive_base = run_dir / "results" / "xcelium_coverage_database"
            archive_base.parent.mkdir(parents=True, exist_ok=True)
            archive = shutil.make_archive(str(archive_base), "zip", root_dir=str(cov_work))
            paths.append(Path(archive))
        return [str(path) for path in paths]

    @staticmethod
    def _export_imc_coverage(run_dir: Path) -> dict[str, Any]:
        executable = shutil.which("imc")
        cov_work = run_dir / "cov_work"
        if not executable:
            return {"status": "unavailable", "available": False, "reason": "IMC executable was not detected; raw Xcelium coverage is preserved."}
        if not cov_work.exists():
            return {"status": "no_database", "available": True, "reason": "Xcelium did not produce cov_work data."}
        results = run_dir / "results"
        results.mkdir(parents=True, exist_ok=True)
        text_report = results / "coverage_imc.txt"
        csv_report = results / "coverage_imc.csv"
        html_report = results / "coverage_imc_html"
        script = (
            f'load -run "{cov_work}"; '
            f'report -detail -out "{text_report}"; '
            f'report -detail -csv -out "{csv_report}"; '
            f'report -html -out "{html_report}"; exit'
        )
        try:
            completed = subprocess.run(
                [executable, "-batch", "-exec", script],
                cwd=str(run_dir),
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except Exception as exc:
            return {"status": "failed", "available": True, "error": str(exc)}
        exports = [str(path) for path in (text_report, csv_report) if path.exists()]
        if html_report.exists():
            exports.append(str(html_report))
        return {
            "status": "exported" if completed.returncode == 0 and exports else "failed",
            "available": True,
            "returncode": completed.returncode,
            "exports": exports,
            "stderr": completed.stderr[-1000:],
        }

    @staticmethod
    def _environment_summary(detection: Any) -> dict[str, Any]:
        details = getattr(detection, "details", {}) or {}
        setup_script = str(details.get("setup_script") or "")
        return {
            "xcelium_path": str(getattr(detection, "path", "") or ""),
            "xcelium_version": str(getattr(detection, "version", "") or ""),
            "uvm_available": bool(getattr(detection, "uvm_available", False)),
            "license_configured": bool(details.get("license_ready")),
            "setup_script_configured": bool(setup_script),
            "setup_script_name": Path(setup_script).name if setup_script else "",
            "setup_variables_applied": list(details.get("setup_applied") or []),
        }

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "test"))[:80]

    @staticmethod
    def _publish_progress(callback: Any, event: dict[str, Any]) -> None:
        if not callback:
            return
        try:
            callback(event)
        except Exception:
            # Progress reporting must never interrupt a simulator run.
            return

    @staticmethod
    def _instrumentation_flags(request: SimulatorRunRequest) -> list[str]:
        """Elaboration-time flags required for waveform probing and coverage scoring.

        ``-access +rwc`` restores read/write/callback visibility that Xcelium
        optimizes away by default (needed to dump waveforms and probe UVM).
        ``-coverage all`` instruments the snapshot so coverage data is actually
        collected; without it at elaboration Xcelium raises NOCOVE at run time.
        """
        flags: list[str] = []
        if request.waveform or request.coverage:
            flags.extend(["-access", "+rwc"])
        if request.coverage:
            flags.extend(["-coverage", "all", "-covoverwrite"])
        return flags

    @staticmethod
    def _analysis_for_log(path: Path):
        from services.verification.uvm_log_parser import parse_uvm_logs

        text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        return parse_uvm_logs([text], simulator="xcelium")

    def _write_mock_logs(self, request: SimulatorRunRequest) -> dict[str, str]:
        logs = {}
        for phase, text in (request.mock_logs or {"compile": ""}).items():
            path = request.run_dir / f"{phase}.log"
            path.write_text(text, encoding="utf-8")
            logs[phase] = str(path)
        return logs

    @staticmethod
    def _run_command(phase: str, command: list[str], cwd: Path, timeout: int) -> SimulatorCommandResult:
        log_path = cwd / f"{phase}.log"
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if not log_path.exists():
                log_path.write_text(f"{completed.stdout}\n{completed.stderr}", encoding="utf-8")
            return SimulatorCommandResult(
                phase=phase,
                command=command,
                returncode=completed.returncode,
                log_path=str(log_path),
                stdout=completed.stdout[-4000:],
                stderr=completed.stderr[-4000:],
            )
        except subprocess.TimeoutExpired as exc:
            log_path.write_text(str(exc), encoding="utf-8")
            return SimulatorCommandResult(
                phase=phase,
                command=command,
                returncode=124,
                log_path=str(log_path),
                timed_out=True,
                stderr=str(exc),
            )

    @staticmethod
    def _persist_result(run_dir: Path, result: SimulatorRunResult) -> None:
        results_dir = run_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "analysis.json").write_text(json.dumps(result.analysis, indent=2), encoding="utf-8")
        (results_dir / "evidence_chain.json").write_text(json.dumps(result.evidence, indent=2), encoding="utf-8")
        (results_dir / "verdict.json").write_text(json.dumps(result.closure_report, indent=2), encoding="utf-8")
        (results_dir / "repair_history.json").write_text(json.dumps(result.repair_history, indent=2), encoding="utf-8")
        (results_dir / "feedback_memory.json").write_text(
            json.dumps(result.closure_report.get("feedback_memory") or {}, indent=2),
            encoding="utf-8",
        )
        (results_dir / "regression.json").write_text(json.dumps(result.regression, indent=2), encoding="utf-8")
        (results_dir / "integrity.json").write_text(json.dumps(result.integrity, indent=2), encoding="utf-8")
        (results_dir / "coverage_closure.json").write_text(json.dumps(result.coverage, indent=2), encoding="utf-8")
        (results_dir / "traceability.json").write_text(json.dumps(result.traceability, indent=2), encoding="utf-8")
        (results_dir / "findings.json").write_text(json.dumps(result.findings, indent=2), encoding="utf-8")
        (results_dir / "run_manifest.json").write_text(
            json.dumps({
                "simulator": result.simulator,
                "run_id": result.run_id,
                "status": result.status,
                "environment": result.environment,
                "commands": [command.to_dict() for command in result.commands],
                "artifacts": result.artifact_paths,
            }, indent=2),
            encoding="utf-8",
        )
