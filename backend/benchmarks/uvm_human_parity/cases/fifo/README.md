# FIFO UVM Human-Parity Case

This starter case reuses the existing FIFO RTL and spec from
`backend/benchmarks/fifo`.

Place human-authored reference UVM files in `human_uvm/` and ChipVerify
generated UVM files in `generated_uvm/`, then run:

```powershell
python backend\scripts\run_uvm_human_parity.py --case backend\benchmarks\uvm_human_parity\cases\fifo
```

This case intentionally does not include fake human references.
