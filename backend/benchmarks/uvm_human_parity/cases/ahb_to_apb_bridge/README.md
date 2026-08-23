# AHB-to-APB Bridge Case

This is an advanced UVM human-parity benchmark case for a multi-interface
bridge DUT. It was imported from the local
`Pre_Silicon-AHB-to_APB-Verification-main` project and keeps the original MIT
license text in `SOURCE_LICENSE_MIT.txt`.

## What It Tests

- Multi-interface UVM architecture with AHB and APB agents
- DUT/interface signal fidelity for `Bridge_Top`
- Single read/write and burst read/write sequence coverage
- Scoreboard prediction and compare quality
- Functional coverage grounding against bridge-level intent
- Reset, ready, address decode, and APB select behavior

## Folder Contract

```text
ahb_to_apb_bridge/
  benchmark.json
  spec.md
  functional_points.json
  rtl/
    DUT.sv
  human_uvm/
    *.sv
    Makefile
  generated_uvm/
    README.md
```

The `human_uvm/` folder is the reference. Put your generated UVM files in
`generated_uvm/` before running the benchmark.

## Run

```powershell
python backend\scripts\run_uvm_human_parity.py --case backend\benchmarks\uvm_human_parity\cases\ahb_to_apb_bridge
```

Reports are written to:

```text
outputs/uvm_human_parity/ahb_to_apb_bridge/
```

This case intentionally does not require `package` or `filelist`, because the
human reference uses `tb_top.sv` includes plus a Makefile compile flow.
