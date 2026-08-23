# AHB-to-APB Bridge UVM Benchmark Case

This benchmark case uses a human-authored UVM VIP for an AHB-to-APB bridge as
the reference implementation. The imported source is MIT licensed; see
`SOURCE_LICENSE_MIT.txt`.

## DUT

- Top module: `Bridge_Top`
- RTL file: `rtl/DUT.sv`
- Clock: `Hclk`
- Reset: `Hresetn`, active low

The DUT converts AHB-side transactions into APB-side transactions. Its top-level
port contract is:

- AHB inputs: `Hclk`, `Hresetn`, `Hwrite`, `Hreadyin`, `Hwdata[31:0]`,
  `Haddr[31:0]`, `Htrans[1:0]`
- APB read input: `Prdata[31:0]`
- AHB outputs: `Hreadyout`, `Hresp[1:0]`, `Hrdata[31:0]`
- APB outputs: `Penable`, `Pwrite`, `Pselx[2:0]`, `Paddr[31:0]`,
  `Pwdata[31:0]`

## Human Reference Shape

The human reference is copied into `human_uvm/` from the Checkpoint 4 UVM VIP.
It uses an include-based top file and a Makefile rather than a package/filelist
flow, so this case does not require the `package` or `filelist` roles.

Expected reference roles:

- AHB and APB interfaces
- AHB and APB sequence items
- AHB and APB sequencers
- AHB and APB drivers
- AHB and APB monitors
- AHB and APB agents
- Shared environment and environment config
- Scoreboard with embedded coverage
- Random, single read/write, and burst read/write tests
- `tb_top.sv` with `run_test`

## Generated Output Contract

Put the files produced by your UVM generator into `generated_uvm/`. A generated
solution should preserve the DUT port contract, implement both AHB and APB
agent paths, include a real scoreboard failure path, and ground coverage in the
functional points listed in `functional_points.json`.

This v1 benchmark is static only. It does not invoke Questa, VCS, Xcelium, or
Cadence tools.
