# UVM Human-Parity Web Interface

This is a small static interface for the UVM human-parity benchmark. It runs in
the browser and does not require Cadence, VCS, Xcelium, a backend server, or
network access.

Open `index.html` in a browser, then upload:

- human-authored UVM files or folder
- ChipVerify-generated UVM files or folder
- optional RTL context files
- optional `functional_points.json`

The page shows the score, missing roles, hallucinated signals, weak scoreboard
findings, coverage gaps, and an improvement plan. It can also download
`scorecard.json`, `issues.json`, and `comparison_report.md`.

For the Python benchmark runner, use:

```powershell
python backend\scripts\run_uvm_human_parity.py --case backend\benchmarks\uvm_human_parity\cases\fifo
```
