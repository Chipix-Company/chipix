# UVM Human-Parity Benchmark

UVM Human-Parity Benchmark is a lightweight, publishable benchmark for comparing
AI-generated UVM testbench collateral against human-authored UVM references.

The first version is intentionally static. It does not require Cadence, VCS,
Xcelium, Questa, licenses, or a running backend. You provide two sets of files:

- `human_uvm/`: UVM files written by a verification engineer
- `generated_uvm/`: UVM files produced by an AI tool such as ChipVerify

The benchmark reports how close the generated UVM is to the human reference,
where it went wrong, and what should be improved in the generator.

## Why This Benchmark Stands Out

Most public LLM hardware benchmarks focus on generated RTL. This benchmark
focuses on generated verification collateral, specifically UVM source quality.
That makes it useful for measuring whether an AI system can produce practical
verification environments, not only design code.

It stands out because it checks issues that matter in real UVM work:

- Missing UVM components such as driver, monitor, scoreboard, coverage, or top
- Hallucinated DUT/interface signals
- Interfaces that do not match the RTL port contract
- Weak scoreboards that only count passes and never fail
- Coverage files that are not grounded in functional intent
- Driver and monitor files missing timing, reset, VIF, or TLM activity
- Placeholder or TODO logic in generated source

The benchmark also has two ways to use it:

- A browser UI for fast manual comparison and report downloads
- A Python CLI for repeatable benchmark runs and automation

## Repository Layout

```text
backend/
  benchmarks/
    uvm_human_parity/
      cases/
        fifo/
          benchmark.json
          human_uvm/
          generated_uvm/
        ahb_to_apb_bridge/
          benchmark.json
          rtl/
          human_uvm/
          generated_uvm/
      web_interface/
        index.html
        styles.css
        app.js
  scripts/
    run_uvm_human_parity.py
  services/
    verification/
      uvm_human_parity.py
      uvm_validator.py
  tests/
    test_uvm_human_parity.py
```

## Included Cases

- `fifo`: starter single-interface case. It intentionally does not include fake
  human references.
- `ahb_to_apb_bridge`: advanced MIT-licensed multi-interface bridge case with
  human-authored AHB/APB UVM agents, tests, scoreboard, and embedded coverage.

Put real human-authored files in `human_uvm/` and generated files in
`generated_uvm/`. The included AHB-to-APB bridge case already contains a human
reference; its `generated_uvm/` folder is the drop zone for your tool output.

## Browser UI

Open the static page:

```text
backend/benchmarks/uvm_human_parity/web_interface/index.html
```

Upload:

- Human UVM reference files or folder
- Generated UVM files or folder
- Optional RTL context files
- Optional `functional_points.json`

The page displays:

- Human-parity score
- Category scores
- Missing roles
- Hallucinated signals
- Weak scoreboard findings
- Coverage grounding gaps
- Improvement plan

It can download:

- `scorecard.json`
- `issues.json`
- `comparison_report.md`

## Python CLI

Run a manifest-based case:

```powershell
python backend\scripts\run_uvm_human_parity.py --case backend\benchmarks\uvm_human_parity\cases\fifo
```

Run all cases in a suite:

```powershell
python backend\scripts\run_uvm_human_parity.py --suite backend\benchmarks\uvm_human_parity\cases
```

Compare two custom folders directly:

```powershell
python backend\scripts\run_uvm_human_parity.py `
  --top fifo `
  --rtl C:\path\to\fifo.sv `
  --human-uvm C:\path\to\human_uvm `
  --generated-uvm C:\path\to\generated_uvm
```

Reports are written to:

```text
outputs/uvm_human_parity/<case_id>/
  scorecard.json
  issues.json
  comparison_report.md
  improvement_plan.md
```

## Benchmark Case Format

Each case uses a `benchmark.json` file:

```json
{
  "id": "fifo",
  "top_module": "fifo",
  "spec_file": "spec.md",
  "rtl_files": ["rtl/fifo.sv"],
  "human_uvm_dir": "human_uvm",
  "generated_uvm_dir": "generated_uvm",
  "clock": "clk",
  "reset": { "name": "rst_n", "active": "low" },
  "required_roles": [
    "package",
    "interface",
    "seq_item",
    "sequencer",
    "driver",
    "monitor",
    "agent",
    "env",
    "scoreboard",
    "coverage",
    "tests",
    "top",
    "filelist"
  ]
}
```

The case format is data-driven, so DUT files can change later without changing
the benchmark runner.

## Scoring

The benchmark produces a score out of 100:

| Category | Points |
| --- | ---: |
| File completeness | 15 |
| Interface fidelity | 15 |
| UVM architecture | 15 |
| Driver/monitor quality | 15 |
| Scoreboard quality | 15 |
| Coverage intent | 10 |
| Sequence/test quality | 10 |
| Maintainability | 5 |

Hard flags include:

- `missing_required_role`
- `hallucinated_signal`
- `missing_interface_signal`
- `weak_scoreboard`
- `missing_covergroup`
- `coverage_not_spec_grounded`
- `placeholder_logic`
- `generated_file_invalid`

## Tests

Run the benchmark tests:

```powershell
python -m pytest backend\tests\test_uvm_human_parity.py -q
```

Expected result:

```text
5 passed
```

## Current Scope

Version 1 is a source-quality benchmark. It does not prove that a generated UVM
environment is simulation-correct. Later versions can add simulator execution,
scoreboard runtime validation, mutation testing, functional coverage closure,
and commercial EDA integration.
