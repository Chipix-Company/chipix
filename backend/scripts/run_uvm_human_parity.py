"""Run the static UVM human-parity benchmark."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.verification.uvm_human_parity import (  # noqa: E402
    BenchmarkCase,
    create_direct_case,
    evaluate_case,
    load_benchmark_case,
    write_result_reports,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        cases = _resolve_cases(args)
        if not cases:
            raise ValueError("No benchmark cases were found.")
        out_root = Path(args.out).resolve()
        results = []
        for case in cases:
            result = evaluate_case(case)
            case_out = out_root / result.case_id
            write_result_reports(result, case_out)
            results.append(result)
            print(
                f"[uvm-human-parity] {result.case_id}: "
                f"{result.human_parity_score}/100 -> {case_out}"
            )
        _write_aggregate(results, out_root)
        return 0
    except Exception as exc:
        print(f"[uvm-human-parity] error: {exc}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare generated UVM files against human-authored UVM references.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", help="Path to one benchmark case directory containing benchmark.json.")
    source.add_argument("--suite", help="Path containing multiple benchmark case directories.")
    source.add_argument("--human-uvm", help="Direct path to the human-authored UVM directory.")

    parser.add_argument("--generated-uvm", help="Direct path to the generated UVM directory.")
    parser.add_argument("--top", help="Top module name for direct path mode.")
    parser.add_argument("--rtl", action="append", default=[], help="RTL context file. Repeat for multiple files.")
    parser.add_argument("--spec", help="Optional spec context file for direct path mode.")
    parser.add_argument("--id", help="Optional case id for direct path mode.")
    parser.add_argument("--out", default="outputs/uvm_human_parity", help="Output root directory.")
    return parser


def _resolve_cases(args: argparse.Namespace) -> list[BenchmarkCase]:
    if args.case:
        return [load_benchmark_case(args.case)]
    if args.suite:
        suite = Path(args.suite).resolve()
        if not suite.exists() or not suite.is_dir():
            raise FileNotFoundError(f"Suite directory not found: {suite}")
        cases = [
            load_benchmark_case(path)
            for path in sorted(suite.iterdir())
            if path.is_dir() and (path / "benchmark.json").exists()
        ]
        return cases

    if not args.generated_uvm:
        raise ValueError("--generated-uvm is required with --human-uvm")
    if not args.top:
        raise ValueError("--top is required with direct path mode")
    return [
        create_direct_case(
            top_module=args.top,
            human_uvm_dir=args.human_uvm,
            generated_uvm_dir=args.generated_uvm,
            rtl_files=args.rtl,
            spec_file=args.spec,
            case_id=args.id,
        )
    ]


def _write_aggregate(results, out_root: Path) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    if not results:
        return
    average = round(sum(item.human_parity_score for item in results) / len(results), 2)
    issue_counts: dict[str, int] = {}
    for result in results:
        for issue in result.issues:
            issue_counts[issue.code] = issue_counts.get(issue.code, 0) + 1
    lines = [
        "# UVM Human-Parity Aggregate",
        "",
        f"- Cases: {len(results)}",
        f"- Average score: {average}/100",
        "",
        "## Case Scores",
        "",
    ]
    for result in sorted(results, key=lambda item: item.human_parity_score):
        lines.append(f"- `{result.case_id}`: {result.human_parity_score}/100")
    lines.extend(["", "## Common Issues", ""])
    if issue_counts:
        for code, count in sorted(issue_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- `{code}`: {count}")
    else:
        lines.append("No issues detected.")
    (out_root / "aggregate_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
