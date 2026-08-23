"""
Category-Specific Corner Case Templates — Knowledge Base

Corner cases organized by design category (storage, processor, peripheral, etc.)
and by cross-cutting traits (CDC, parameterized, etc.).

Every template is traceable to one of:
  - Official protocol specifications (ARM AMBA, IEEE, NXP)
  - Open-source verification plans (OpenTitan, PULP Platform)
  - Industry best practices (DVCON papers, Accellera UVM cookbook)

Each template is a *pattern* — when applied to a concrete design, the
``trigger_signals`` list is matched against actual design signals to
determine applicability and to populate ``signals_involved``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CornerCaseTemplate:
    """A template for a corner case that may apply to a specific design.

    Templates are generic patterns.  When applied to a specific design,
    ``trigger_signals`` are matched against actual design signals to
    determine applicability and to populate ``signals_involved``.

    Attributes:
        id:                    Unique identifier (e.g. ``"CC-STOR-001"``).
        category:              Design category this applies to (or ``"universal"``).
        corner_type:           Functional category of the corner case.
        severity:              ``"P0"`` must verify · ``"P1"`` should · ``"P2"`` nice to have.
        title:                 Short descriptive title.
        description:           Detailed description of what to test.
        trigger_signals:       Signal name substrings — if *any* match a real signal
                               in the design the template is considered applicable.
        verification_approach: ``"formal"`` | ``"uvm"`` | ``"unitsim"`` | ``"manual"``.
        provenance:            Human-readable source reference.
        provenance_type:       ``"spec"`` | ``"open_source_vplan"`` | ``"industry_practice"``
                               | ``"structural_inference"``.
        rationale:             Why this corner case matters (what class of bug it catches).
        stimulus_hint:         Optional hint for stimulus generation.
        check_hint:            Optional hint for checking / scoreboard.
        requires_trait:        Only apply if the design has this trait (e.g. ``"multi_clock"``).
    """

    id: str
    category: str
    corner_type: str
    severity: str
    title: str
    description: str
    trigger_signals: List[str]
    verification_approach: str
    provenance: str
    provenance_type: str
    rationale: str
    stimulus_hint: str = ""
    check_hint: str = ""
    requires_trait: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# UNIVERSAL — applies to ALL designs regardless of category
# ═══════════════════════════════════════════════════════════════════════════

UNIVERSAL_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-UNIV-001", category="universal", corner_type="reset",
        severity="P0",
        title="Reset during idle state",
        description="Assert reset while DUT is idle with no active transactions. Verify all outputs reach their documented reset values and all internal state machines return to their initial state.",
        trigger_signals=["rst", "reset", "rstn", "rst_n", "areset"],
        verification_approach="unitsim",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Ensures basic reset functionality — the most fundamental requirement of any digital design.",
        stimulus_hint="Drive reset for several cycles during idle, then deassert and verify.",
        check_hint="Compare every output against documented reset value.",
    ),
    CornerCaseTemplate(
        id="CC-UNIV-002", category="universal", corner_type="reset",
        severity="P0",
        title="Reset during active operation",
        description="Assert reset while the DUT is mid-transaction. Verify graceful termination: no data corruption, no output glitches during reset, and clean return to idle after deassertion.",
        trigger_signals=["rst", "reset", "rstn", "rst_n", "areset"],
        verification_approach="formal",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Reset mid-operation is the #1 source of state machine bugs in real silicon.",
        stimulus_hint="Start a transaction, assert reset mid-way, deassert, verify clean state.",
        check_hint="No output glitch during reset; outputs reach reset values within specified cycles.",
    ),
    CornerCaseTemplate(
        id="CC-UNIV-003", category="universal", corner_type="reset",
        severity="P1",
        title="Reset deassert timing",
        description="Verify behaviour at the exact cycle of reset deassertion. For synchronous resets, check on the active clock edge. For asynchronous resets, verify the reset synchroniser chain.",
        trigger_signals=["rst", "reset", "rstn", "rst_n"],
        verification_approach="formal",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Incorrect reset release timing causes metastability or one-cycle glitches.",
    ),
    CornerCaseTemplate(
        id="CC-UNIV-004", category="universal", corner_type="concurrent",
        severity="P0",
        title="Back-to-back transactions with zero idle",
        description="Send transactions with zero idle cycles between them. The DUT must handle the maximum throughput case without dropping data or missing handshakes.",
        trigger_signals=["valid", "ready", "enable", "wr_en", "rd_en", "req"],
        verification_approach="uvm",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Pipe-lining bugs often only manifest at maximum throughput.",
        stimulus_hint="Drive valid/enable continuously without deasserting between transactions.",
    ),
    CornerCaseTemplate(
        id="CC-UNIV-005", category="universal", corner_type="concurrent",
        severity="P1",
        title="Single transaction followed by long idle",
        description="Issue a single transaction and then remain idle for many cycles. Verify outputs are stable during idle and no spurious activity occurs.",
        trigger_signals=["valid", "ready", "enable", "wr_en", "rd_en"],
        verification_approach="unitsim",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Detects leaky state machines that keep running after a transaction completes.",
    ),
    CornerCaseTemplate(
        id="CC-UNIV-006", category="universal", corner_type="concurrent",
        severity="P1",
        title="Maximum throughput sustained burst",
        description="Sustain maximum-rate transfers for an extended period (hundreds to thousands of cycles). Verify no counter overflow, no resource leak, and no gradual drift in behaviour.",
        trigger_signals=["valid", "ready", "enable"],
        verification_approach="uvm",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Internal counters, FIFOs, or state variables may overflow during sustained operation.",
    ),
    CornerCaseTemplate(
        id="CC-UNIV-007", category="universal", corner_type="power",
        severity="P2",
        title="Clock gating interaction",
        description="Verify that enabling/disabling clock gating during active operation does not corrupt state or lose data.",
        trigger_signals=["clk_en", "clk_gate", "cg_en"],
        verification_approach="formal",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Clock gating bugs are notoriously hard to debug post-silicon.",
        requires_trait="has_clock_gating",
    ),
    CornerCaseTemplate(
        id="CC-UNIV-008", category="universal", corner_type="config",
        severity="P1",
        title="Control change while busy",
        description="Change a control/configuration input while the DUT reports busy or active. Verify the design either defers the change to a safe boundary or rejects it cleanly.",
        trigger_signals=["busy", "active", "ctrl", "control", "config", "mode"],
        verification_approach="uvm",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Hot control changes are a generic source of corrupted state across peripherals, accelerators, and controllers.",
        stimulus_hint="Start an operation, wait for busy/active, then change one control field.",
        check_hint="No partial transaction is corrupted; accepted changes occur only at a documented safe point.",
    ),
    CornerCaseTemplate(
        id="CC-UNIV-009", category="universal", corner_type="status",
        severity="P1",
        title="Status update and software read same cycle",
        description="Read a status output or status register on the exact cycle hardware updates it. Verify the observed value is consistent and no status event is lost.",
        trigger_signals=["status", "stat", "pending", "flag", "event", "rd_en"],
        verification_approach="formal",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Status read/update races cause software-visible missed events and hard-to-debug firmware failures.",
        check_hint="A hardware event remains visible until software observes or clears it according to spec.",
    ),
    CornerCaseTemplate(
        id="CC-UNIV-010", category="universal", corner_type="ordering",
        severity="P1",
        title="Request cancellation at completion boundary",
        description="Cancel, flush, or clear an operation on the same cycle it completes. Verify completion and cancellation precedence is deterministic and documented.",
        trigger_signals=["cancel", "flush", "clear", "done", "complete", "valid"],
        verification_approach="formal",
        provenance="Common DV practice",
        provenance_type="industry_practice",
        rationale="Cancel/done races can double-complete, lose completion, or leave resources allocated forever.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# STORAGE — FIFO, cache, register file, SRAM controller
# ═══════════════════════════════════════════════════════════════════════════

STORAGE_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-STOR-001", category="storage", corner_type="boundary",
        severity="P0",
        title="Write when storage is exactly full",
        description="Attempt a write when the storage element is at maximum capacity. Verify the full flag is asserted, no data corruption occurs, and overflow is handled as specified (ignored, error flag, or overwrite).",
        trigger_signals=["full", "fifo_full", "overflow", "ovfl", "wr_ptr", "count"],
        verification_approach="formal",
        provenance="Common FIFO verification practice; OpenTitan UART DV plan",
        provenance_type="open_source_vplan",
        rationale="Overflow is the most common FIFO bug — corrupts data or causes hangs.",
        stimulus_hint="Fill storage to capacity, then attempt one more write.",
        check_hint="full flag asserted; data integrity of existing entries preserved.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-002", category="storage", corner_type="boundary",
        severity="P0",
        title="Read when storage is exactly empty",
        description="Attempt a read when the storage element has zero entries. Verify the empty flag is asserted and the design handles underflow as specified (returns zero, asserts error, or ignores).",
        trigger_signals=["empty", "fifo_empty", "underflow", "udfl", "rd_ptr", "count"],
        verification_approach="formal",
        provenance="Common FIFO verification practice; OpenTitan UART DV plan",
        provenance_type="open_source_vplan",
        rationale="Underflow reads return stale or garbage data — silent data corruption.",
        stimulus_hint="Drain storage completely, then attempt one more read.",
        check_hint="empty flag asserted; no stale data appears on output.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-003", category="storage", corner_type="concurrent",
        severity="P0",
        title="Simultaneous read and write at arbitrary depth",
        description="Issue read and write on the same cycle when storage is neither full nor empty. Verify both operations complete correctly and the count/flags update atomically.",
        trigger_signals=["wr_en", "rd_en", "push", "pop", "wr_ptr", "rd_ptr"],
        verification_approach="uvm",
        provenance="Common FIFO verification practice",
        provenance_type="industry_practice",
        rationale="Concurrent R+W is the most common source of pointer logic bugs.",
        stimulus_hint="Assert both write and read enables on the same clock edge.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-004", category="storage", corner_type="concurrent",
        severity="P0",
        title="Simultaneous read and write when exactly full",
        description="Issue read and write simultaneously when storage is at full capacity. The read should free one slot while the write fills it — net count stays the same. Verify full flag behaviour.",
        trigger_signals=["full", "wr_en", "rd_en", "push", "pop"],
        verification_approach="formal",
        provenance="FIFO verification practice",
        provenance_type="industry_practice",
        rationale="Subtle flag update ordering bugs: does full deassert momentarily?",
    ),
    CornerCaseTemplate(
        id="CC-STOR-005", category="storage", corner_type="concurrent",
        severity="P0",
        title="Simultaneous read and write when exactly empty",
        description="Issue read and write simultaneously when storage is empty. The write adds one entry while the read tries to consume — verify whether the read sees the just-written data or the empty condition.",
        trigger_signals=["empty", "wr_en", "rd_en", "push", "pop"],
        verification_approach="formal",
        provenance="FIFO verification practice",
        provenance_type="industry_practice",
        rationale="Does empty deassert immediately or one cycle later? Design-dependent but must be tested.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-006", category="storage", corner_type="boundary",
        severity="P0",
        title="Fill completely then drain completely",
        description="Write entries until full, then read all entries until empty. Verify every data value read matches what was written and that flags transition correctly throughout.",
        trigger_signals=["full", "empty", "wr_en", "rd_en", "data_in", "data_out"],
        verification_approach="uvm",
        provenance="Common FIFO verification practice",
        provenance_type="industry_practice",
        rationale="End-to-end data integrity check across the full capacity.",
        check_hint="Data read back in correct order matches data written.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-007", category="storage", corner_type="boundary",
        severity="P1",
        title="Single-element depth operation (DEPTH=1 or single entry)",
        description="Operate storage with only one element capacity. Verify that full and empty flags toggle correctly and data passes through.",
        trigger_signals=["full", "empty", "depth", "count"],
        verification_approach="unitsim",
        provenance="FIFO verification practice",
        provenance_type="industry_practice",
        rationale="Boundary case — pointer comparison logic often breaks at minimum depth.",
        requires_trait="parameterized",
    ),
    CornerCaseTemplate(
        id="CC-STOR-008", category="storage", corner_type="boundary",
        severity="P0",
        title="Pointer/counter wraparound at maximum depth",
        description="Run enough write/read operations to cause the internal pointers or counters to wrap around from their maximum value to zero. Verify no data loss or flag error at the wrap boundary.",
        trigger_signals=["wr_ptr", "rd_ptr", "ptr", "pointer", "count", "cnt"],
        verification_approach="formal",
        provenance="Common FIFO verification practice",
        provenance_type="industry_practice",
        rationale="Pointer wraparound bugs are the #2 most common FIFO bug after overflow.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-009", category="storage", corner_type="ordering",
        severity="P0",
        title="Data ordering integrity (FIFO order preserved)",
        description="Write a known sequence of values, then read them back. Verify the output order exactly matches the input order (first-in, first-out).",
        trigger_signals=["data_in", "data_out", "rdata", "wdata", "din", "dout"],
        verification_approach="formal",
        provenance="FIFO fundamental property",
        provenance_type="industry_practice",
        rationale="The defining property of a FIFO — if ordering is broken, the design is wrong.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-010", category="storage", corner_type="boundary",
        severity="P1",
        title="Almost-full to full transition",
        description="Observe the transition from almost-full to full. Verify that the almost-full flag asserts at the correct threshold and that the full flag follows exactly when expected.",
        trigger_signals=["almost_full", "afull", "nearly_full", "watermark", "full"],
        verification_approach="uvm",
        provenance="OpenTitan UART DV plan — watermark interrupt testing",
        provenance_type="open_source_vplan",
        rationale="Threshold-based flags (watermarks) drive interrupt logic — wrong threshold = missed interrupt.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-011", category="storage", corner_type="boundary",
        severity="P1",
        title="Almost-empty to empty transition",
        description="Observe the transition from almost-empty to empty. Verify threshold flag timing.",
        trigger_signals=["almost_empty", "aempty", "nearly_empty", "empty"],
        verification_approach="uvm",
        provenance="OpenTitan UART DV plan",
        provenance_type="open_source_vplan",
        rationale="Read-side watermark bugs cause data loss in flow-controlled systems.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-012", category="storage", corner_type="boundary",
        severity="P1",
        title="Rapid full-to-empty-to-full cycling",
        description="Rapidly cycle the storage between full and empty multiple times. Verify flags and pointers remain consistent through repeated extreme transitions.",
        trigger_signals=["full", "empty", "count", "wr_ptr", "rd_ptr"],
        verification_approach="uvm",
        provenance="FIFO verification practice",
        provenance_type="industry_practice",
        rationale="Stress test for pointer comparison logic — exposes race conditions.",
    ),
    CornerCaseTemplate(
        id="CC-STOR-013", category="storage", corner_type="boundary",
        severity="P1",
        title="Operation at maximum parameter value (MAX_DEPTH)",
        description="Instantiate with the maximum supported depth parameter and exercise full/empty boundary conditions. Verify pointer widths are sufficient.",
        trigger_signals=["depth", "fifo_depth", "mem_depth", "addr_width"],
        verification_approach="unitsim",
        provenance="Parameterized design verification practice",
        provenance_type="industry_practice",
        rationale="Pointer width miscalculation at max depth causes silent wrap errors.",
        requires_trait="parameterized",
    ),
    CornerCaseTemplate(
        id="CC-STOR-014", category="storage", corner_type="boundary",
        severity="P1",
        title="Operation at minimum parameter value (DEPTH=1)",
        description="Instantiate with minimum depth. Full and empty may be asserted simultaneously — verify correct behaviour.",
        trigger_signals=["depth", "fifo_depth"],
        verification_approach="unitsim",
        provenance="Parameterized design verification practice",
        provenance_type="industry_practice",
        rationale="Minimum-parameter edge case often untested — breaks pointer comparison.",
        requires_trait="parameterized",
    ),
    CornerCaseTemplate(
        id="CC-STOR-015", category="storage", corner_type="cdc",
        severity="P0",
        title="Gray code correctness at pointer boundary",
        description="Verify that pointer values encoded in Gray code change only one bit per increment, especially at the wrap-around boundary (max → 0).",
        trigger_signals=["gray", "wr_ptr_gray", "rd_ptr_gray", "bin2gray"],
        verification_approach="formal",
        provenance="Async FIFO CDC verification — Cummings SNUG 2002 paper",
        provenance_type="industry_practice",
        rationale="Multi-bit change in Gray code at CDC boundary causes metastable pointer reads.",
        requires_trait="multi_clock",
    ),
    CornerCaseTemplate(
        id="CC-STOR-016", category="storage", corner_type="cdc",
        severity="P1",
        title="Pessimistic flag behaviour in async FIFO",
        description="In an asynchronous FIFO, full and empty flags may be pessimistic due to synchronisation latency. Verify: full is never optimistically deasserted (claiming space when there is none), and empty is never optimistically deasserted (claiming data when there is none).",
        trigger_signals=["full", "empty", "sync", "gray", "wr_clk", "rd_clk"],
        verification_approach="formal",
        provenance="Async FIFO CDC practice — Cummings SNUG 2002",
        provenance_type="industry_practice",
        rationale="Optimistic flag = data corruption. Pessimistic = reduced throughput (acceptable).",
        requires_trait="multi_clock",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# PROTOCOL_BRIDGE — AXI-APB, AHB-AXI, protocol converters
# ═══════════════════════════════════════════════════════════════════════════

PROTOCOL_BRIDGE_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-BRDG-001", category="protocol_bridge", corner_type="backpressure",
        severity="P0",
        title="Downstream backpressure (slave not ready)",
        description="Downstream slave deasserts ready/PREADY and holds it low for many cycles. Verify the bridge correctly stalls the upstream interface and preserves data.",
        trigger_signals=["ready", "pready", "hready", "tready", "stall", "wait"],
        verification_approach="uvm",
        provenance="Protocol bridge verification practice",
        provenance_type="industry_practice",
        rationale="Backpressure handling errors cause data loss or protocol violations upstream.",
    ),
    CornerCaseTemplate(
        id="CC-BRDG-002", category="protocol_bridge", corner_type="backpressure",
        severity="P0",
        title="Upstream stall (master pauses mid-transfer)",
        description="Upstream master deasserts valid mid-burst or mid-transfer. Verify the bridge preserves state and resumes correctly when valid reasserts.",
        trigger_signals=["valid", "awvalid", "arvalid", "wvalid", "tvalid"],
        verification_approach="uvm",
        provenance="Protocol bridge verification practice",
        provenance_type="industry_practice",
        rationale="State corruption during upstream pause leads to protocol violations on downstream.",
    ),
    CornerCaseTemplate(
        id="CC-BRDG-003", category="protocol_bridge", corner_type="error",
        severity="P0",
        title="Error propagation across bridge",
        description="Inject error response on downstream side (PSLVERR, HRESP ERROR, RRESP/BRESP SLVERR). Verify the correct error response propagates to the upstream interface.",
        trigger_signals=["error", "err", "slverr", "pslverr", "hresp", "bresp", "rresp"],
        verification_approach="uvm",
        provenance="ARM AMBA spec — error response mapping",
        provenance_type="spec",
        rationale="Error response mismap causes silent failures — upstream thinks transfer succeeded.",
    ),
    CornerCaseTemplate(
        id="CC-BRDG-004", category="protocol_bridge", corner_type="ordering",
        severity="P0",
        title="Transaction ordering preservation across bridge",
        description="Issue multiple transactions with same ID through the bridge. Verify responses arrive in the same order on the upstream side.",
        trigger_signals=["awid", "arid", "bid", "rid", "id", "tag"],
        verification_approach="uvm",
        provenance="ARM IHI 0022, Section A6.3 — same-ID ordering",
        provenance_type="spec",
        rationale="Order violation across bridge causes wrong data delivered to wrong transaction.",
    ),
    CornerCaseTemplate(
        id="CC-BRDG-005", category="protocol_bridge", corner_type="resource",
        severity="P1",
        title="Maximum outstanding transactions through bridge",
        description="Issue transactions until the bridge's internal buffering is exhausted. Verify the bridge either stalls correctly or handles overflow as specified.",
        trigger_signals=["outstanding", "pending", "depth", "fifo", "buffer"],
        verification_approach="uvm",
        provenance="Protocol bridge verification practice",
        provenance_type="industry_practice",
        rationale="Internal buffer overflow in bridge causes data loss or deadlock.",
    ),
    CornerCaseTemplate(
        id="CC-BRDG-006", category="protocol_bridge", corner_type="timing",
        severity="P1",
        title="Protocol timeout (one side unresponsive)",
        description="Simulate one side of the bridge becoming unresponsive (ready stuck low or slave never responds). Verify timeout handling if implemented.",
        trigger_signals=["timeout", "watchdog", "timer", "ready", "pready"],
        verification_approach="uvm",
        provenance="Protocol bridge verification practice",
        provenance_type="industry_practice",
        rationale="Hung transaction = system deadlock. Timeout recovery is critical.",
    ),
    CornerCaseTemplate(
        id="CC-BRDG-007", category="protocol_bridge", corner_type="boundary",
        severity="P1",
        title="Address alignment mismatch between protocols",
        description="Issue a transfer with address alignment valid on upstream but problematic on downstream (e.g. unaligned access on APB). Verify bridge handles the mismatch.",
        trigger_signals=["addr", "paddr", "haddr", "awaddr", "araddr"],
        verification_approach="uvm",
        provenance="AXI-APB bridge verification practice",
        provenance_type="industry_practice",
        rationale="Address alignment bugs cause wrong register or memory location access.",
    ),
    CornerCaseTemplate(
        id="CC-BRDG-008", category="protocol_bridge", corner_type="boundary",
        severity="P1",
        title="Data width mismatch handling (upsizing/downsizing)",
        description="If the bridge converts between different data widths (e.g. 64-bit AXI to 32-bit APB), verify byte lane mapping, WSTRB translation, and read data assembly.",
        trigger_signals=["wstrb", "strb", "byte_en", "wdata", "rdata"],
        verification_approach="uvm",
        provenance="Protocol bridge verification practice",
        provenance_type="industry_practice",
        rationale="Width conversion bugs corrupt specific byte lanes — often only caught with random addresses.",
    ),
    CornerCaseTemplate(
        id="CC-BRDG-009", category="protocol_bridge", corner_type="protocol",
        severity="P0",
        title="Burst conversion (e.g. AXI burst to APB single beats)",
        description="Issue an AXI burst (INCR, WRAP) and verify the bridge correctly splits it into multiple APB single-beat transfers with correct address incrementing.",
        trigger_signals=["burst", "awburst", "arburst", "awlen", "arlen", "incr", "wrap"],
        verification_approach="uvm",
        provenance="ARM AMBA spec — burst type definitions",
        provenance_type="spec",
        rationale="Burst splitting errors cause wrong addresses and lost beats.",
    ),
    CornerCaseTemplate(
        id="CC-BRDG-010", category="protocol_bridge", corner_type="reset",
        severity="P1",
        title="Asymmetric reset (reset one side only)",
        description="If the bridge spans clock/reset domains, assert reset on one side only. Verify the other side handles the partner's reset gracefully.",
        trigger_signals=["rst", "reset", "rstn", "rst_n"],
        verification_approach="formal",
        provenance="Protocol bridge verification practice",
        provenance_type="industry_practice",
        rationale="Asymmetric reset can leave the bridge in a half-initialised inconsistent state.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# PROCESSOR_CORE — CPU pipeline, ALU, decode
# ═══════════════════════════════════════════════════════════════════════════

PROCESSOR_CORE_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-PROC-001", category="processor_core", corner_type="hazard",
        severity="P0",
        title="RAW data hazard (read-after-write same register)",
        description="Consecutive instructions where the second reads a register just written by the first. Verify forwarding/bypass logic delivers the correct value.",
        trigger_signals=["opcode", "rs1", "rs2", "rd", "forward", "bypass", "hazard", "stall"],
        verification_approach="uvm",
        provenance="RISC-V DV practice; Patterson & Hennessy pipeline hazard taxonomy",
        provenance_type="industry_practice",
        rationale="RAW hazards are the most common pipeline bug — wrong data forwarded = wrong result.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-002", category="processor_core", corner_type="hazard",
        severity="P0",
        title="WAW data hazard (write-after-write same register)",
        description="Two instructions both write to the same register. Verify the later instruction's value wins.",
        trigger_signals=["rd", "writeback", "wr_en", "register_file"],
        verification_approach="formal",
        provenance="Pipeline hazard taxonomy",
        provenance_type="industry_practice",
        rationale="WAW in out-of-order pipelines can result in stale value persisting.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-003", category="processor_core", corner_type="hazard",
        severity="P1",
        title="WAR data hazard (write-after-read same register)",
        description="Instruction reads a register, followed by an instruction that writes to it. Verify the read gets the old value.",
        trigger_signals=["rs1", "rs2", "rd", "register_file"],
        verification_approach="formal",
        provenance="Pipeline hazard taxonomy",
        provenance_type="industry_practice",
        rationale="Relevant for out-of-order and superscalar pipelines.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-004", category="processor_core", corner_type="hazard",
        severity="P0",
        title="Control hazard (branch followed by dependent instructions)",
        description="A branch or jump instruction followed by instructions in the delay slot or speculative window. Verify correct flush on misprediction.",
        trigger_signals=["branch", "jump", "pc", "flush", "predict", "target", "taken"],
        verification_approach="uvm",
        provenance="Pipeline hazard taxonomy; RISC-V DV practice",
        provenance_type="industry_practice",
        rationale="Control hazard mishandling executes instructions that should have been squashed.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-005", category="processor_core", corner_type="error",
        severity="P0",
        title="Exception during multi-cycle operation",
        description="Trigger an exception (illegal instruction, access fault, etc.) while a multi-cycle instruction is executing. Verify pipeline flush and correct exception handler entry.",
        trigger_signals=["exception", "trap", "fault", "illegal", "mcause", "mtvec"],
        verification_approach="uvm",
        provenance="RISC-V ISA manual — exception handling",
        provenance_type="spec",
        rationale="Exception mid-operation that doesn't fully flush leads to architectural state corruption.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-006", category="processor_core", corner_type="concurrent",
        severity="P0",
        title="Interrupt during pipeline stall",
        description="Assert an external interrupt while the pipeline is stalled (e.g. on a cache miss or multi-cycle divide). Verify interrupt is taken at the correct point.",
        trigger_signals=["irq", "interrupt", "stall", "wait", "pending"],
        verification_approach="uvm",
        provenance="RISC-V ISA manual — interrupt priority and delegation",
        provenance_type="spec",
        rationale="Interrupt during stall can be lost or taken at wrong PC.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-007", category="processor_core", corner_type="protocol",
        severity="P0",
        title="Pipeline flush correctness",
        description="After a flush event (branch mispredict, exception, fence.i), verify all stages downstream of the flush point are correctly invalidated and no partial results are committed.",
        trigger_signals=["flush", "squash", "kill", "invalidate", "pipeline"],
        verification_approach="formal",
        provenance="Pipeline verification practice",
        provenance_type="industry_practice",
        rationale="Incomplete flush leaves stale instructions that corrupt architectural state.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-008", category="processor_core", corner_type="timing",
        severity="P1",
        title="CSR read/write timing",
        description="Read and write CSR registers in consecutive instructions. Verify read-after-write returns the new value and side effects are correct.",
        trigger_signals=["csr", "mstatus", "mie", "mip", "mcause", "csrw", "csrr"],
        verification_approach="uvm",
        provenance="RISC-V ISA manual — CSR instructions",
        provenance_type="spec",
        rationale="CSR access ordering bugs affect interrupt enable/disable timing.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-009", category="processor_core", corner_type="error",
        severity="P0",
        title="Illegal instruction exception",
        description="Feed an invalid/reserved opcode to the decoder. Verify the illegal instruction exception is raised and PC is saved correctly.",
        trigger_signals=["opcode", "decode", "illegal", "exception", "trap"],
        verification_approach="uvm",
        provenance="RISC-V ISA manual",
        provenance_type="spec",
        rationale="Missing illegal-instruction detection is a security vulnerability.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-010", category="processor_core", corner_type="concurrent",
        severity="P1",
        title="Nested exception/interrupt",
        description="Trigger an exception while already in an exception handler, or an interrupt while handling another interrupt. Verify correct nesting or detection of double-fault.",
        trigger_signals=["exception", "trap", "irq", "interrupt", "nested", "mstatus"],
        verification_approach="uvm",
        provenance="RISC-V ISA manual — trap delegation",
        provenance_type="spec",
        rationale="Nested trap without proper save causes CSR corruption.",
    ),
    CornerCaseTemplate(
        id="CC-PROC-011", category="processor_core", corner_type="hazard",
        severity="P1",
        title="Branch prediction miss recovery",
        description="Force a branch misprediction and verify the pipeline correctly flushes speculative instructions and resumes from the correct target.",
        trigger_signals=["predict", "branch", "mispredict", "btb", "bht", "taken", "target"],
        verification_approach="uvm",
        provenance="Pipeline verification practice",
        provenance_type="industry_practice",
        rationale="Misprediction recovery bugs execute wrong instructions silently.",
        requires_trait="has_branch_predictor",
    ),
    CornerCaseTemplate(
        id="CC-PROC-012", category="processor_core", corner_type="error",
        severity="P1",
        title="Memory alignment fault",
        description="Issue a load/store with an address not aligned to the access size. Verify the correct exception is raised (or misalignment is handled if supported).",
        trigger_signals=["addr", "load", "store", "align", "fault", "exception"],
        verification_approach="uvm",
        provenance="RISC-V ISA manual — load/store alignment",
        provenance_type="spec",
        rationale="Missing alignment check is a security and correctness vulnerability.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# PERIPHERAL — UART, SPI, I2C, GPIO, timer, watchdog
# ═══════════════════════════════════════════════════════════════════════════

PERIPHERAL_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-PERI-001", category="peripheral", corner_type="concurrent",
        severity="P0",
        title="Register write during active transfer",
        description="Write to a configuration or data register while the peripheral is actively processing a transfer. Verify the write either takes effect at a safe point or is rejected.",
        trigger_signals=["paddr", "pwrite", "pwdata", "haddr", "hwdata", "wr_en", "config"],
        verification_approach="uvm",
        provenance="OpenTitan peripheral DV methodology",
        provenance_type="open_source_vplan",
        rationale="Mid-transfer config change is the most common peripheral bug — corrupts ongoing data.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-002", category="peripheral", corner_type="config",
        severity="P0",
        title="Configuration change during active operation",
        description="Modify mode, baud rate, prescaler, or other configuration while the peripheral is actively operating. Verify graceful handling.",
        trigger_signals=["config", "ctrl", "control", "mode", "baud", "prescaler", "divider"],
        verification_approach="uvm",
        provenance="OpenTitan UART/SPI DV plans",
        provenance_type="open_source_vplan",
        rationale="Hot config change mid-operation causes protocol violations on the serial line.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-003", category="peripheral", corner_type="concurrent",
        severity="P0",
        title="Interrupt while interrupt mask is set",
        description="Trigger an interrupt condition while the corresponding interrupt mask bit is set (interrupts disabled). Verify the interrupt is not fired but the status/pending bit is still set.",
        trigger_signals=["irq", "interrupt", "mask", "imask", "int_en", "ien", "pending"],
        verification_approach="unitsim",
        provenance="OpenTitan peripheral DV — interrupt testing",
        provenance_type="open_source_vplan",
        rationale="Masked interrupt that doesn't set pending bit = lost interrupt when mask clears.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-004", category="peripheral", corner_type="concurrent",
        severity="P1",
        title="Interrupt clear race condition",
        description="Clear an interrupt status bit at the same time a new interrupt event occurs. Verify the new event is not lost.",
        trigger_signals=["int_clr", "irq_clr", "iclr", "int_ack", "status", "pending"],
        verification_approach="formal",
        provenance="Peripheral DV practice",
        provenance_type="industry_practice",
        rationale="Clear-on-write1 race with new event = lost interrupt = system hang.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-005", category="peripheral", corner_type="concurrent",
        severity="P1",
        title="Back-to-back register access",
        description="Issue register reads and writes in consecutive clock cycles with no idle between them. Verify the peripheral handles maximum-rate bus access.",
        trigger_signals=["psel", "penable", "pready", "hsel", "hready", "wr_en", "rd_en"],
        verification_approach="uvm",
        provenance="Peripheral DV practice",
        provenance_type="industry_practice",
        rationale="Back-to-back access exposes pipeline stall bugs in register interface.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-006", category="peripheral", corner_type="config",
        severity="P1",
        title="Invalid configuration write",
        description="Write reserved or out-of-range values to configuration registers. Verify the design rejects them (error flag, ignore, or clamp to valid range).",
        trigger_signals=["config", "ctrl", "mode", "baud", "prescaler"],
        verification_approach="unitsim",
        provenance="OpenTitan peripheral DV methodology",
        provenance_type="open_source_vplan",
        rationale="Invalid config that is silently accepted causes undefined behaviour.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-007", category="peripheral", corner_type="concurrent",
        severity="P1",
        title="Status register read during update",
        description="Read a status register at the exact cycle it is being updated by hardware. Verify the read returns a consistent value (not a partial update).",
        trigger_signals=["status", "stat", "flags", "state"],
        verification_approach="formal",
        provenance="Peripheral DV practice",
        provenance_type="industry_practice",
        rationale="Partial status read causes software to make wrong decisions.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-008", category="peripheral", corner_type="boundary",
        severity="P1",
        title="FIFO watermark interrupt timing",
        description="Configure the FIFO watermark level and verify the interrupt fires at exactly the right fill level, including edge cases (watermark=0, watermark=max).",
        trigger_signals=["watermark", "threshold", "level", "fifo", "tx_fifo", "rx_fifo"],
        verification_approach="uvm",
        provenance="OpenTitan UART DV plan — watermark interrupt test",
        provenance_type="open_source_vplan",
        rationale="Wrong watermark threshold = interrupt fires too early or too late.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-009", category="peripheral", corner_type="config",
        severity="P0",
        title="Enable/disable during active operation",
        description="Toggle the peripheral enable bit while it is actively processing. Verify it stops gracefully and can be re-enabled cleanly.",
        trigger_signals=["enable", "en", "start", "go", "run", "active"],
        verification_approach="uvm",
        provenance="Peripheral DV practice",
        provenance_type="industry_practice",
        rationale="Disable during active leaves partial state — re-enable may see corrupt data.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-010", category="peripheral", corner_type="error",
        severity="P1",
        title="Error interrupt flag sticky behaviour",
        description="Trigger an error condition and verify the error flag remains asserted (sticky) until explicitly cleared by software.",
        trigger_signals=["error", "err", "parity_err", "frame_err", "overrun", "break_err"],
        verification_approach="unitsim",
        provenance="OpenTitan UART DV plan — error interrupt testing",
        provenance_type="open_source_vplan",
        rationale="Non-sticky error flag that auto-clears = software misses the error.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-011", category="peripheral", corner_type="register",
        severity="P1",
        title="Write-one-clear and hardware-set same cycle",
        description="Clear a W1C status bit in the same cycle hardware sets the bit again. Verify the new hardware event wins or remains pending according to the register spec.",
        trigger_signals=["w1c", "clear", "clr", "status", "pending", "event"],
        verification_approach="formal",
        provenance="Peripheral CSR verification practice",
        provenance_type="industry_practice",
        rationale="W1C plus hardware-set races are a common way to lose interrupts and status events.",
        stimulus_hint="Write 1 to clear the status bit while forcing the event source high.",
        check_hint="A new event is still observable after the clear transaction completes.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-012", category="peripheral", corner_type="register",
        severity="P1",
        title="Read-only field ignores software writes",
        description="Attempt to write all-ones and all-zeroes to read-only status fields. Verify hardware-owned fields are unchanged by software writes.",
        trigger_signals=["status", "ro", "read_only", "pwrite", "wr_en", "csr"],
        verification_approach="unitsim",
        provenance="Peripheral CSR verification practice",
        provenance_type="industry_practice",
        rationale="Writable read-only status fields let software forge hardware state and break debug/sign-off assumptions.",
    ),
    CornerCaseTemplate(
        id="CC-PERI-013", category="peripheral", corner_type="register",
        severity="P1",
        title="Shadowed register commit atomicity",
        description="For shadowed or double-buffered configuration, update only part of the shadow value and trigger commit. Verify no partial configuration reaches the active datapath.",
        trigger_signals=["shadow", "commit", "update", "cfg", "config", "lock"],
        verification_approach="uvm",
        provenance="Peripheral CSR verification practice",
        provenance_type="industry_practice",
        rationale="Partial shadow commit can apply an illegal mixed configuration for one or more cycles.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# CRYPTO_SECURITY — AES, SHA, RNG, key manager
# ═══════════════════════════════════════════════════════════════════════════

CRYPTO_SECURITY_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-CRYP-001", category="crypto_security", corner_type="concurrent",
        severity="P0",
        title="Key change during active encryption/decryption",
        description="Change the encryption key while a block is being processed mid-round. Verify the design either completes with the old key or aborts cleanly — no mixed-key output.",
        trigger_signals=["key", "key_in", "key_wr", "key_valid", "cipher", "round"],
        verification_approach="uvm",
        provenance="Crypto verification practice",
        provenance_type="industry_practice",
        rationale="Mixed-key encryption produces unpredictable and potentially exploitable output.",
    ),
    CornerCaseTemplate(
        id="CC-CRYP-002", category="crypto_security", corner_type="boundary",
        severity="P0",
        title="All-zeros input block",
        description="Process a data block that is all zeros. Verify the cipher produces the correct NIST test vector output and does not shortcut.",
        trigger_signals=["data_in", "plaintext", "msg", "block"],
        verification_approach="unitsim",
        provenance="NIST test vectors",
        provenance_type="spec",
        rationale="All-zeros input may trigger degenerate paths in cipher logic.",
    ),
    CornerCaseTemplate(
        id="CC-CRYP-003", category="crypto_security", corner_type="boundary",
        severity="P0",
        title="All-ones input block",
        description="Process a data block that is all ones (0xFFFF...). Verify correct cipher output per known test vector.",
        trigger_signals=["data_in", "plaintext", "msg", "block"],
        verification_approach="unitsim",
        provenance="NIST test vectors",
        provenance_type="spec",
        rationale="All-ones may trigger different internal paths than normal data.",
    ),
    CornerCaseTemplate(
        id="CC-CRYP-004", category="crypto_security", corner_type="concurrent",
        severity="P0",
        title="Abort and restart mid-operation",
        description="Abort a crypto operation mid-round and immediately start a new one. Verify the internal state is fully cleared and the new operation produces correct output.",
        trigger_signals=["abort", "cancel", "start", "init", "busy", "done"],
        verification_approach="uvm",
        provenance="Crypto verification practice",
        provenance_type="industry_practice",
        rationale="Stale internal state after abort contaminates the next operation.",
    ),
    CornerCaseTemplate(
        id="CC-CRYP-005", category="crypto_security", corner_type="security",
        severity="P0",
        title="Key isolation — key not leaked on data output",
        description="Verify that key material never appears on the data output port, status registers, or debug interfaces under any sequence of operations.",
        trigger_signals=["key", "data_out", "ciphertext", "debug", "status"],
        verification_approach="formal",
        provenance="Security best practice; Common Criteria",
        provenance_type="industry_practice",
        rationale="Key leakage on output ports is a critical security vulnerability.",
    ),
    CornerCaseTemplate(
        id="CC-CRYP-006", category="crypto_security", corner_type="security",
        severity="P1",
        title="IV/nonce reuse detection",
        description="Reuse the same IV or nonce with the same key. Verify the design detects this (if specified) or produces correct (but insecure) output.",
        trigger_signals=["iv", "nonce", "init_vector", "ctr"],
        verification_approach="uvm",
        provenance="Crypto best practice — NIST SP 800-38A",
        provenance_type="spec",
        rationale="IV reuse in CTR/GCM mode completely breaks confidentiality.",
    ),
    CornerCaseTemplate(
        id="CC-CRYP-007", category="crypto_security", corner_type="boundary",
        severity="P1",
        title="Non-standard message length (N × block_size + 1 bytes)",
        description="Process a message whose length is not a multiple of the block size. Verify padding and final block handling are correct.",
        trigger_signals=["msg_len", "length", "byte_count", "last", "pad"],
        verification_approach="uvm",
        provenance="OpenTitan HMAC DV plan — non-standard message lengths",
        provenance_type="open_source_vplan",
        rationale="Off-by-one in final block processing causes wrong hash/cipher output.",
    ),
    CornerCaseTemplate(
        id="CC-CRYP-008", category="crypto_security", corner_type="concurrent",
        severity="P1",
        title="Context save/restore correctness",
        description="Save the internal state mid-operation, process a different message, then restore and resume. Verify the resumed operation produces the same output as uninterrupted processing.",
        trigger_signals=["context", "save", "restore", "state", "digest"],
        verification_approach="uvm",
        provenance="OpenTitan HMAC DV plan — context switching",
        provenance_type="open_source_vplan",
        rationale="Incorrect context save/restore produces wrong hash for multi-part messages.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# DMA_DATA_MOVER — DMA engine, scatter-gather, descriptor manager
# ═══════════════════════════════════════════════════════════════════════════

DMA_DATA_MOVER_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-DMA-001", category="dma_data_mover", corner_type="boundary",
        severity="P0",
        title="Zero-length transfer request",
        description="Request a DMA transfer with byte_count=0 or length=0. Verify the DMA engine does not hang and handles it as specified (no-op, error, or interrupt).",
        trigger_signals=["byte_count", "length", "size", "transfer_size", "num_bytes"],
        verification_approach="unitsim",
        provenance="DMA verification practice",
        provenance_type="industry_practice",
        rationale="Zero-length transfer that isn't handled causes infinite loop or bus hang.",
    ),
    CornerCaseTemplate(
        id="CC-DMA-002", category="dma_data_mover", corner_type="error",
        severity="P0",
        title="Malformed or null descriptor in chain",
        description="Place an invalid or null next-descriptor pointer in the descriptor chain. Verify the DMA detects the error and signals it (interrupt, error status).",
        trigger_signals=["descriptor", "desc", "next_desc", "chain", "linked_list"],
        verification_approach="uvm",
        provenance="DMA verification practice",
        provenance_type="industry_practice",
        rationale="Null descriptor chase = bus hang or access to address 0x0.",
    ),
    CornerCaseTemplate(
        id="CC-DMA-003", category="dma_data_mover", corner_type="concurrent",
        severity="P0",
        title="Multi-channel simultaneous operation",
        description="Activate multiple DMA channels simultaneously transferring to/from different addresses. Verify no data mixing between channels.",
        trigger_signals=["channel", "ch", "dma_ch", "ch_sel", "ch_en"],
        verification_approach="uvm",
        provenance="DMA verification practice",
        provenance_type="industry_practice",
        rationale="Channel cross-contamination = silent data corruption in memory.",
    ),
    CornerCaseTemplate(
        id="CC-DMA-004", category="dma_data_mover", corner_type="boundary",
        severity="P1",
        title="Unaligned source or destination address",
        description="Configure a transfer where the source or destination address is not aligned to the bus width. Verify the DMA handles byte-lane steering correctly or reports an error.",
        trigger_signals=["src_addr", "dst_addr", "source", "dest", "addr"],
        verification_approach="uvm",
        provenance="DMA verification practice",
        provenance_type="industry_practice",
        rationale="Unaligned address with wrong byte-lane mapping corrupts adjacent memory.",
    ),
    CornerCaseTemplate(
        id="CC-DMA-005", category="dma_data_mover", corner_type="boundary",
        severity="P1",
        title="Transfer crossing 4KB page boundary",
        description="Configure a transfer that crosses a 4KB address boundary (relevant for AXI compliance). Verify the DMA splits the burst correctly.",
        trigger_signals=["addr", "src_addr", "dst_addr", "burst", "boundary"],
        verification_approach="uvm",
        provenance="ARM IHI 0022 — AXI 4KB boundary rule",
        provenance_type="spec",
        rationale="4KB crossing in a single AXI burst is a protocol violation.",
    ),
    CornerCaseTemplate(
        id="CC-DMA-006", category="dma_data_mover", corner_type="concurrent",
        severity="P0",
        title="Abort in-flight transfer",
        description="Abort a DMA transfer while it is actively moving data. Verify the channel stops, reports partial completion status, and the bus is not left in a broken state.",
        trigger_signals=["abort", "cancel", "stop", "disable", "ch_en"],
        verification_approach="uvm",
        provenance="DMA verification practice",
        provenance_type="industry_practice",
        rationale="Abort that leaves AXI transaction open = bus deadlock.",
    ),
    CornerCaseTemplate(
        id="CC-DMA-007", category="dma_data_mover", corner_type="boundary",
        severity="P1",
        title="Descriptor ring wraparound",
        description="Configure a circular descriptor ring and run enough transfers to wrap around. Verify the DMA follows the ring correctly without losing descriptors.",
        trigger_signals=["descriptor", "ring", "circular", "wrap", "next_desc"],
        verification_approach="uvm",
        provenance="DMA verification practice",
        provenance_type="industry_practice",
        rationale="Ring wrap bug = processes stale descriptor = wrong transfer.",
    ),
    CornerCaseTemplate(
        id="CC-DMA-008", category="dma_data_mover", corner_type="concurrent",
        severity="P0",
        title="Interrupt on completion correctness",
        description="Verify the completion interrupt fires at exactly the right point — after the last byte is written to memory, not after the last bus request is issued.",
        trigger_signals=["done", "complete", "irq", "interrupt", "tc", "eot"],
        verification_approach="unitsim",
        provenance="DMA verification practice",
        provenance_type="industry_practice",
        rationale="Early completion interrupt = software reads incomplete data from memory.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# BUS_INTERCONNECT — crossbar, switch, NoC router
# ═══════════════════════════════════════════════════════════════════════════

BUS_INTERCONNECT_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-BUS-001", category="bus_interconnect", corner_type="concurrent",
        severity="P0",
        title="Simultaneous requests from all masters",
        description="All masters issue requests on the same cycle. Verify the arbiter grants one, stalls the rest, and eventually serves all.",
        trigger_signals=["request", "req", "grant", "gnt", "master", "arbiter"],
        verification_approach="uvm",
        provenance="Interconnect verification practice",
        provenance_type="industry_practice",
        rationale="All-masters-simultaneous is the worst-case arbitration scenario.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-002", category="bus_interconnect", corner_type="concurrent",
        severity="P0",
        title="Arbitration fairness — no starvation",
        description="One master issues continuous traffic while others issue sparse requests. Verify no master is permanently starved of bus access.",
        trigger_signals=["grant", "gnt", "priority", "round_robin", "fair", "starve"],
        verification_approach="formal",
        provenance="Interconnect verification practice; AMBA spec fairness requirements",
        provenance_type="industry_practice",
        rationale="Master starvation = that subsystem hangs and potentially the whole SoC.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-003", category="bus_interconnect", corner_type="concurrent",
        severity="P0",
        title="Deadlock detection (circular dependency)",
        description="Create a scenario where masters hold resources and wait for each other (circular dependency). Verify the interconnect prevents or detects deadlock.",
        trigger_signals=["lock", "exclusive", "grant", "request", "pending"],
        verification_approach="formal",
        provenance="Interconnect verification practice",
        provenance_type="industry_practice",
        rationale="Deadlock = permanent system hang requiring reset.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-004", category="bus_interconnect", corner_type="protocol",
        severity="P1",
        title="Locked/exclusive access handling",
        description="Issue locked (AHB) or exclusive (AXI) accesses through the interconnect. Verify the lock holds the arbiter and exclusive monitor works correctly.",
        trigger_signals=["lock", "hmastlock", "exclusive", "exokay", "arlock", "awlock"],
        verification_approach="uvm",
        provenance="ARM AMBA spec — locked/exclusive access",
        provenance_type="spec",
        rationale="Broken exclusive access = atomic operations fail = race conditions in software.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-005", category="bus_interconnect", corner_type="error",
        severity="P0",
        title="Default slave response for invalid address",
        description="Issue a transaction to an address not mapped to any slave. Verify the interconnect returns a DECERR (AXI) or ERROR (AHB) response from the default slave.",
        trigger_signals=["decerr", "error", "default", "decode", "addr"],
        verification_approach="uvm",
        provenance="ARM AMBA spec — default slave",
        provenance_type="spec",
        rationale="Missing decode error = master hangs waiting for response that never comes.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-006", category="bus_interconnect", corner_type="resource",
        severity="P1",
        title="Maximum outstanding transactions per master",
        description="One master issues the maximum number of outstanding transactions. Verify the interconnect stalls correctly and does not drop or reorder transactions.",
        trigger_signals=["outstanding", "pending", "depth", "awid", "arid"],
        verification_approach="uvm",
        provenance="Interconnect verification practice",
        provenance_type="industry_practice",
        rationale="Outstanding overflow = dropped transaction or ID collision.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-007", category="bus_interconnect", corner_type="concurrent",
        severity="P1",
        title="QoS priority inversion",
        description="Low-priority master holds a resource while a high-priority master requests it. Verify QoS mechanism prevents prolonged priority inversion.",
        trigger_signals=["qos", "priority", "awqos", "arqos", "urgent"],
        verification_approach="uvm",
        provenance="ARM AMBA QoS specification",
        provenance_type="spec",
        rationale="Priority inversion delays real-time masters — violates latency SLAs.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-008", category="bus_interconnect", corner_type="error",
        severity="P0",
        title="Error response routing to correct master",
        description="Slave returns an error. Verify the error response is delivered to the master that issued the original transaction, not another master.",
        trigger_signals=["error", "bresp", "rresp", "hresp", "slverr", "decerr", "bid", "rid"],
        verification_approach="uvm",
        provenance="Interconnect verification practice",
        provenance_type="industry_practice",
        rationale="Error routed to wrong master = one master thinks it succeeded, another gets spurious error.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-009", category="bus_interconnect", corner_type="ordering",
        severity="P0",
        title="Response ID/order preservation",
        description="Issue multiple outstanding reads and writes with distinct IDs. Verify every response returns to the correct master and preserves ordering rules for the same ID.",
        trigger_signals=["id", "awid", "arid", "rid", "bid", "order", "outstanding"],
        verification_approach="uvm",
        provenance="Interconnect verification practice",
        provenance_type="industry_practice",
        rationale="ID/order bugs are catastrophic because responses are delivered to the wrong transaction context.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-010", category="bus_interconnect", corner_type="security",
        severity="P0",
        title="Address firewall deny path",
        description="Attempt accesses from an unauthorized master to a protected address region. Verify the interconnect blocks the transfer and returns the documented error response.",
        trigger_signals=["firewall", "secure", "priv", "prot", "addr", "error"],
        verification_approach="uvm",
        provenance="SoC security verification practice",
        provenance_type="industry_practice",
        rationale="Interconnect-level access control is often the last line of defense between IP blocks.",
    ),
    CornerCaseTemplate(
        id="CC-BUS-011", category="bus_interconnect", corner_type="reset",
        severity="P0",
        title="Reset with outstanding transactions",
        description="Assert reset while the interconnect has outstanding transactions. Verify all pending state is cleared or completed according to spec without orphaned responses.",
        trigger_signals=["reset", "rst", "outstanding", "pending", "valid", "ready"],
        verification_approach="formal",
        provenance="Interconnect verification practice",
        provenance_type="industry_practice",
        rationale="Reset with outstanding state can leave masters waiting forever or receive stale responses after reset.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# MEMORY_CONTROLLER — DDR, LPDDR, SRAM controller, ECC
# ═══════════════════════════════════════════════════════════════════════════

MEMORY_CONTROLLER_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-MEM-001", category="memory_controller", corner_type="concurrent",
        severity="P0",
        title="Bank conflict (simultaneous access to same bank/row)",
        description="Issue read and write requests that target the same memory bank simultaneously. Verify the controller serialises them correctly.",
        trigger_signals=["bank", "row", "col", "ba", "bg", "bank_addr"],
        verification_approach="uvm",
        provenance="Memory controller verification practice",
        provenance_type="industry_practice",
        rationale="Bank conflict mishandling corrupts data in the conflicting row.",
    ),
    CornerCaseTemplate(
        id="CC-MEM-002", category="memory_controller", corner_type="timing",
        severity="P0",
        title="Refresh timing compliance",
        description="Verify the controller issues refresh commands within the required timing window (tREFI) and does not skip or delay refreshes under load.",
        trigger_signals=["refresh", "ref", "trefi", "tref", "auto_refresh"],
        verification_approach="formal",
        provenance="JEDEC DDR specification",
        provenance_type="spec",
        rationale="Missed refresh = data loss in DRAM cells.",
    ),
    CornerCaseTemplate(
        id="CC-MEM-003", category="memory_controller", corner_type="ordering",
        severity="P0",
        title="Read-after-write to same address (RAW)",
        description="Write to an address then immediately read from it. Verify the read returns the just-written data, not stale data from a previous write.",
        trigger_signals=["addr", "wdata", "rdata", "wr_en", "rd_en"],
        verification_approach="uvm",
        provenance="Memory controller verification practice",
        provenance_type="industry_practice",
        rationale="RAW hazard in controller pipeline returns stale data.",
    ),
    CornerCaseTemplate(
        id="CC-MEM-004", category="memory_controller", corner_type="error",
        severity="P0",
        title="ECC single-bit error correction",
        description="Inject a single-bit error into read data. Verify the ECC logic corrects it and reports the correction via status/interrupt.",
        trigger_signals=["ecc", "parity", "syndrome", "correctable", "sec", "ded"],
        verification_approach="unitsim",
        provenance="Memory controller verification practice; JEDEC",
        provenance_type="industry_practice",
        rationale="ECC must correct single-bit errors transparently.",
    ),
    CornerCaseTemplate(
        id="CC-MEM-005", category="memory_controller", corner_type="error",
        severity="P0",
        title="ECC multi-bit error detection",
        description="Inject a double-bit error into read data. Verify the ECC logic detects it (DED) and raises an uncorrectable error signal.",
        trigger_signals=["ecc", "ded", "uncorrectable", "ue", "multi_bit_err"],
        verification_approach="unitsim",
        provenance="Memory controller verification practice; JEDEC",
        provenance_type="industry_practice",
        rationale="Undetected multi-bit error = silent data corruption.",
    ),
    CornerCaseTemplate(
        id="CC-MEM-006", category="memory_controller", corner_type="power",
        severity="P1",
        title="Power mode transition during access",
        description="Transition memory between power states (active, self-refresh, power-down) while an access is pending. Verify correct sequencing.",
        trigger_signals=["power", "self_refresh", "power_down", "cke", "sleep"],
        verification_approach="uvm",
        provenance="JEDEC DDR specification",
        provenance_type="spec",
        rationale="Power transition during active access corrupts the access and may damage DRAM timing.",
    ),
    CornerCaseTemplate(
        id="CC-MEM-007", category="memory_controller", corner_type="boundary",
        severity="P1",
        title="Burst crossing row/page boundary",
        description="Issue a burst that crosses a DRAM row boundary (requiring a precharge-activate sequence mid-burst). Verify correct address wrapping.",
        trigger_signals=["burst", "row", "page", "precharge", "activate", "boundary"],
        verification_approach="uvm",
        provenance="Memory controller verification practice",
        provenance_type="industry_practice",
        rationale="Row boundary crossing bug = data written to wrong row.",
    ),
    CornerCaseTemplate(
        id="CC-MEM-008", category="memory_controller", corner_type="register",
        severity="P1",
        title="Byte-enable write masking",
        description="Write every byte-enable/strobe pattern to the same word. Verify enabled bytes update and disabled bytes preserve their previous value.",
        trigger_signals=["byte_en", "be", "strobe", "strb", "wstrb", "wmask"],
        verification_approach="formal",
        provenance="Memory controller verification practice",
        provenance_type="industry_practice",
        rationale="Masking bugs corrupt adjacent bytes in memories, CSR files, and cache lines.",
    ),
    CornerCaseTemplate(
        id="CC-MEM-009", category="memory_controller", corner_type="ordering",
        severity="P1",
        title="Write-read collision same address same cycle",
        description="Drive a read and write to the same address in the same cycle. Verify read-during-write behavior matches the documented policy: old data, new data, or undefined with error.",
        trigger_signals=["rd_en", "wr_en", "addr", "same_addr", "collision"],
        verification_approach="formal",
        provenance="Memory macro/controller verification practice",
        provenance_type="industry_practice",
        rationale="Read-during-write policy differences are a classic source of simulation/silicon mismatch.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# DSP_DATAPATH — FIR, FFT, MAC, CRC, encoder/decoder
# ═══════════════════════════════════════════════════════════════════════════

DSP_DATAPATH_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-DSP-001", category="dsp_datapath", corner_type="boundary",
        severity="P0",
        title="Maximum positive input value (overflow test)",
        description="Apply the maximum positive value to all inputs simultaneously. Verify either correct saturation or correct wrap-around (per design spec).",
        trigger_signals=["data_in", "sample", "input", "a", "b", "operand"],
        verification_approach="unitsim",
        provenance="DSP verification practice",
        provenance_type="industry_practice",
        rationale="Unsigned/signed overflow produces wildly wrong output — critical for signal integrity.",
    ),
    CornerCaseTemplate(
        id="CC-DSP-002", category="dsp_datapath", corner_type="boundary",
        severity="P0",
        title="Maximum negative input value (underflow test)",
        description="Apply the most negative value (signed) to all inputs. Verify correct saturation or wrap behaviour.",
        trigger_signals=["data_in", "sample", "input", "a", "b", "operand"],
        verification_approach="unitsim",
        provenance="DSP verification practice",
        provenance_type="industry_practice",
        rationale="Signed underflow in multiply-accumulate produces wrong sign and magnitude.",
    ),
    CornerCaseTemplate(
        id="CC-DSP-003", category="dsp_datapath", corner_type="boundary",
        severity="P1",
        title="Zero input (identity/passthrough check)",
        description="Apply zero to all inputs. Verify the output is zero (or the correct identity value for the operation).",
        trigger_signals=["data_in", "sample", "input", "coeff"],
        verification_approach="unitsim",
        provenance="DSP verification practice",
        provenance_type="industry_practice",
        rationale="Non-zero output for zero input = bias error or DC offset bug.",
    ),
    CornerCaseTemplate(
        id="CC-DSP-004", category="dsp_datapath", corner_type="boundary",
        severity="P0",
        title="Accumulator saturation",
        description="Run enough multiply-accumulate operations to overflow the accumulator. Verify saturation logic clamps to max/min rather than wrapping.",
        trigger_signals=["accumulator", "acc", "mac", "sum", "saturate", "overflow"],
        verification_approach="formal",
        provenance="DSP verification practice",
        provenance_type="industry_practice",
        rationale="Accumulator wrap-around produces large opposite-sign spikes in output.",
    ),
    CornerCaseTemplate(
        id="CC-DSP-005", category="dsp_datapath", corner_type="concurrent",
        severity="P1",
        title="Pipeline bubble insertion and handling",
        description="Insert idle cycles (bubbles) in the datapath pipeline by deasserting input valid. Verify the pipeline handles gaps without corrupting other samples.",
        trigger_signals=["valid", "data_valid", "enable", "pipeline", "stage"],
        verification_approach="uvm",
        provenance="DSP verification practice",
        provenance_type="industry_practice",
        rationale="Pipeline bubble that shifts sample positions = wrong filter output.",
    ),
    CornerCaseTemplate(
        id="CC-DSP-006", category="dsp_datapath", corner_type="concurrent",
        severity="P1",
        title="Back-to-back processing (no idle between blocks)",
        description="Feed data blocks with zero idle cycles between them. Verify the output is continuous and correct at block boundaries.",
        trigger_signals=["valid", "last", "frame", "block", "data_in"],
        verification_approach="uvm",
        provenance="DSP verification practice",
        provenance_type="industry_practice",
        rationale="Block boundary bugs cause first/last sample corruption.",
    ),
    CornerCaseTemplate(
        id="CC-DSP-007", category="dsp_datapath", corner_type="concurrent",
        severity="P1",
        title="Coefficient reload during processing",
        description="Update filter coefficients while the filter is actively processing samples. Verify transition handling (old coefficients for current, new for next, or immediate switch).",
        trigger_signals=["coeff", "coefficient", "tap", "weight", "reload", "update"],
        verification_approach="uvm",
        provenance="DSP verification practice",
        provenance_type="industry_practice",
        rationale="Partial coefficient update mid-filter produces transient incorrect output.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# CLOCK_RESET_PMU — PLL, clock gate, reset sync, power domain
# ═══════════════════════════════════════════════════════════════════════════

CLOCK_RESET_PMU_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-CRP-001", category="clock_reset_pmu", corner_type="timing",
        severity="P0",
        title="PLL lock time verification",
        description="After configuring PLL parameters, measure the time to achieve lock. Verify it is within the specified range and that the lock indicator is accurate.",
        trigger_signals=["pll", "lock", "locked", "stable", "vco", "divider"],
        verification_approach="unitsim",
        provenance="Clock domain verification practice",
        provenance_type="industry_practice",
        rationale="Using PLL output before lock produces unstable clock — corrupts all downstream logic.",
    ),
    CornerCaseTemplate(
        id="CC-CRP-002", category="clock_reset_pmu", corner_type="concurrent",
        severity="P0",
        title="Glitch-free clock switching",
        description="Switch between clock sources. Verify no runt pulses or glitches on the output clock during the transition.",
        trigger_signals=["clk_sel", "clk_mux", "clk_switch", "clk_src", "mux_sel"],
        verification_approach="formal",
        provenance="Clock domain verification practice",
        provenance_type="industry_practice",
        rationale="Clock glitch causes metastability in every flip-flop in the domain.",
    ),
    CornerCaseTemplate(
        id="CC-CRP-003", category="clock_reset_pmu", corner_type="cdc",
        severity="P0",
        title="Reset synchroniser chain correctness",
        description="Verify the reset synchroniser correctly synchronises async reset deassertion to the target clock domain with no metastability.",
        trigger_signals=["reset_sync", "rst_sync", "sync_rst", "areset", "rstn"],
        verification_approach="formal",
        provenance="CDC verification practice — Cummings",
        provenance_type="industry_practice",
        rationale="Unsynchronised reset release causes some flops to exit reset one cycle early.",
    ),
    CornerCaseTemplate(
        id="CC-CRP-004", category="clock_reset_pmu", corner_type="power",
        severity="P0",
        title="Power domain transition sequence",
        description="Walk through the complete power-up and power-down sequences. Verify isolation, retention, and clamp cells activate in the correct order.",
        trigger_signals=["power", "pwr", "domain", "isolate", "retain", "clamp", "sleep", "wakeup"],
        verification_approach="uvm",
        provenance="UPF/CPF power-aware verification practice",
        provenance_type="industry_practice",
        rationale="Wrong power sequence order causes crowbar current or logic corruption.",
    ),
    CornerCaseTemplate(
        id="CC-CRP-005", category="clock_reset_pmu", corner_type="power",
        severity="P0",
        title="Retention register save/restore",
        description="Power down a domain, then power it back up. Verify all retention registers have their saved values after restore.",
        trigger_signals=["retention", "retain", "save", "restore", "power_down", "power_up"],
        verification_approach="uvm",
        provenance="UPF/CPF power-aware verification practice",
        provenance_type="industry_practice",
        rationale="Lost retention = software must re-initialise everything = boot time regression.",
    ),
    CornerCaseTemplate(
        id="CC-CRP-006", category="clock_reset_pmu", corner_type="timing",
        severity="P0",
        title="Isolation cell activation timing",
        description="Verify isolation cells activate before power is removed from the isolated domain and deactivate after power is stable.",
        trigger_signals=["isolate", "isolation", "iso_en", "clamp", "power_good"],
        verification_approach="formal",
        provenance="UPF/CPF power-aware verification practice",
        provenance_type="industry_practice",
        rationale="Late isolation activation = floating outputs drive X into active domain.",
    ),
    CornerCaseTemplate(
        id="CC-CRP-007", category="clock_reset_pmu", corner_type="timing",
        severity="P0",
        title="Clock gate enable stability",
        description="Toggle clock-gate enable near the active clock edge. Verify the gated clock has no runt pulse and the enable is sampled only at a safe phase.",
        trigger_signals=["clk_en", "cg_en", "clock_gate", "gated_clk", "latch_en"],
        verification_approach="formal",
        provenance="Clock gating verification practice",
        provenance_type="industry_practice",
        rationale="Clock-gate enable glitches can create extra edges or missing edges inside the gated domain.",
    ),
    CornerCaseTemplate(
        id="CC-CRP-008", category="clock_reset_pmu", corner_type="power",
        severity="P0",
        title="Wakeup request during power-down entry",
        description="Assert wakeup in the same window that the domain is entering low power. Verify the PMU resolves the race without losing wakeup or entering an illegal state.",
        trigger_signals=["wakeup", "sleep", "power_down", "pwr_req", "pwr_ack", "idle"],
        verification_approach="uvm",
        provenance="Power-management verification practice",
        provenance_type="industry_practice",
        rationale="Lost wakeup during sleep entry is a high-impact silicon bug in mobile and always-on systems.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# ARBITER_SCHEDULER — standalone arbiters and schedulers
# ═══════════════════════════════════════════════════════════════════════════

ARBITER_SCHEDULER_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-ARB-001", category="arbiter_scheduler", corner_type="concurrent",
        severity="P0",
        title="All requestors active simultaneously",
        description="Assert all request inputs on the same cycle. Verify exactly one grant is issued and the selection follows the configured priority scheme.",
        trigger_signals=["request", "req", "grant", "gnt", "arbiter"],
        verification_approach="uvm",
        provenance="Arbiter verification practice",
        provenance_type="industry_practice",
        rationale="Multiple simultaneous grants = bus contention = electrical damage or data corruption.",
    ),
    CornerCaseTemplate(
        id="CC-ARB-002", category="arbiter_scheduler", corner_type="boundary",
        severity="P1",
        title="Single requestor (no contention)",
        description="Only one requestor is active. Verify it receives the grant immediately without unnecessary delay.",
        trigger_signals=["request", "req", "grant", "gnt"],
        verification_approach="unitsim",
        provenance="Arbiter verification practice",
        provenance_type="industry_practice",
        rationale="Single-requestor latency test — should be zero-cycle or one-cycle grant.",
    ),
    CornerCaseTemplate(
        id="CC-ARB-003", category="arbiter_scheduler", corner_type="concurrent",
        severity="P0",
        title="Priority inversion detection",
        description="Low-priority requestor holds the grant while high-priority requestor is waiting. Verify the arbiter resolves the inversion within bounded time.",
        trigger_signals=["priority", "pri", "level", "weight", "urgent"],
        verification_approach="formal",
        provenance="Arbiter verification practice",
        provenance_type="industry_practice",
        rationale="Unbounded priority inversion violates real-time latency requirements.",
    ),
    CornerCaseTemplate(
        id="CC-ARB-004", category="arbiter_scheduler", corner_type="concurrent",
        severity="P0",
        title="Starvation under sustained high-priority load",
        description="High-priority requestor issues continuous traffic. Verify low-priority requestors eventually get service (anti-starvation guarantee).",
        trigger_signals=["grant", "gnt", "starve", "fair", "round_robin", "token"],
        verification_approach="uvm",
        provenance="Arbiter verification practice",
        provenance_type="industry_practice",
        rationale="Starvation = low-priority subsystem hangs permanently.",
    ),
    CornerCaseTemplate(
        id="CC-ARB-005", category="arbiter_scheduler", corner_type="timing",
        severity="P1",
        title="Grant hold/release timing",
        description="Verify the grant signal asserts and deasserts with correct timing relative to the request and bus transfer completion.",
        trigger_signals=["grant", "gnt", "lock", "hold", "release"],
        verification_approach="formal",
        provenance="Arbiter verification practice",
        provenance_type="industry_practice",
        rationale="Grant held too long = other requestors starved. Released too early = transfer interrupted.",
    ),
    CornerCaseTemplate(
        id="CC-ARB-006", category="arbiter_scheduler", corner_type="protocol",
        severity="P1",
        title="Lock/unlock sequence correctness",
        description="Requestor acquires a lock (locked transfer), holds it across multiple beats, then releases. Verify the arbiter does not re-arbitrate during locked phase.",
        trigger_signals=["lock", "locked", "hmastlock", "arlock", "awlock"],
        verification_approach="uvm",
        provenance="ARM AMBA spec — locked access",
        provenance_type="spec",
        rationale="Lock violation = atomic operation broken = race condition in software.",
    ),
    CornerCaseTemplate(
        id="CC-ARB-007", category="arbiter_scheduler", corner_type="reset",
        severity="P1",
        title="Reset while grant is active",
        description="Assert reset while a requestor holds grant. Verify grant is released, internal owner state clears, and the next post-reset grant starts from the documented policy.",
        trigger_signals=["reset", "rst", "grant", "gnt", "owner", "token"],
        verification_approach="formal",
        provenance="Arbiter verification practice",
        provenance_type="industry_practice",
        rationale="Stale owner state after reset can grant a resource to the wrong requestor.",
    ),
    CornerCaseTemplate(
        id="CC-ARB-008", category="arbiter_scheduler", corner_type="concurrent",
        severity="P1",
        title="Request drops before grant",
        description="Assert a request, then deassert it before grant arrives while other requestors remain active. Verify the arbiter does not issue a stale grant.",
        trigger_signals=["request", "req", "grant", "gnt", "pending"],
        verification_approach="formal",
        provenance="Arbiter verification practice",
        provenance_type="industry_practice",
        rationale="Stale grant after request withdrawal can start an unintended transaction.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# TEST_DEBUG — JTAG, debug module, trace, scan
# ═══════════════════════════════════════════════════════════════════════════

TEST_DEBUG_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-DBG-001", category="test_debug", corner_type="protocol",
        severity="P0",
        title="TAP state machine reset (5×TMS=1)",
        description="Drive TMS high for 5 consecutive TCK cycles. Verify the TAP controller reaches Test-Logic-Reset regardless of its current state.",
        trigger_signals=["tms", "tck", "tdi", "tdo", "trst", "tap", "jtag"],
        verification_approach="formal",
        provenance="IEEE 1149.1",
        provenance_type="spec",
        rationale="TAP reset failure = JTAG debug and boundary scan completely non-functional.",
    ),
    CornerCaseTemplate(
        id="CC-DBG-002", category="test_debug", corner_type="concurrent",
        severity="P0",
        title="Debug halt during active execution",
        description="Issue a debug halt request while the processor is actively executing instructions. Verify the processor enters debug mode cleanly with correct PC and state saved.",
        trigger_signals=["halt", "debug", "dm", "debug_req", "ebreak"],
        verification_approach="uvm",
        provenance="RISC-V Debug Specification 0.13",
        provenance_type="spec",
        rationale="Unclean halt = saved PC is wrong = resume executes from wrong location.",
    ),
    CornerCaseTemplate(
        id="CC-DBG-003", category="test_debug", corner_type="concurrent",
        severity="P1",
        title="Breakpoint hit and resume",
        description="Set a breakpoint, let execution hit it, inspect state, then resume. Verify execution continues correctly from the breakpoint address.",
        trigger_signals=["breakpoint", "bp", "trigger", "tdata", "mcontrol"],
        verification_approach="uvm",
        provenance="RISC-V Debug Specification 0.13",
        provenance_type="spec",
        rationale="Resume from wrong PC or with corrupted state = debugger is unreliable.",
    ),
    CornerCaseTemplate(
        id="CC-DBG-004", category="test_debug", corner_type="timing",
        severity="P1",
        title="Debug register access timing",
        description="Read and write debug registers (DMI, abstractcs, data0) with minimum and maximum timing. Verify no access is dropped.",
        trigger_signals=["dmi", "abstract", "data0", "debug_reg", "command"],
        verification_approach="unitsim",
        provenance="RISC-V Debug Specification 0.13",
        provenance_type="spec",
        rationale="Dropped debug register access = debugger fails silently.",
    ),
    CornerCaseTemplate(
        id="CC-DBG-005", category="test_debug", corner_type="boundary",
        severity="P1",
        title="Trace buffer overflow handling",
        description="Fill the trace buffer completely and verify the behaviour — wrap-around, stop, or trigger interrupt as configured.",
        trigger_signals=["trace", "trace_buf", "trace_full", "overflow", "wrap"],
        verification_approach="uvm",
        provenance="Trace verification practice",
        provenance_type="industry_practice",
        rationale="Trace overflow that corrupts non-trace memory = system crash.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# NETWORK_PACKET — Ethernet MAC, packet parser, scheduler
# ═══════════════════════════════════════════════════════════════════════════

NETWORK_PACKET_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-NET-001", category="network_packet", corner_type="boundary",
        severity="P0",
        title="Minimum-size packet processing",
        description="Send the minimum valid packet size (e.g. 64 bytes for Ethernet). Verify it is processed correctly without being dropped as a runt.",
        trigger_signals=["packet", "frame", "length", "byte_count", "sof", "eof"],
        verification_approach="uvm",
        provenance="IEEE 802.3 — Ethernet minimum frame size",
        provenance_type="spec",
        rationale="Minimum packet logic often differs from general path — easy to break.",
    ),
    CornerCaseTemplate(
        id="CC-NET-002", category="network_packet", corner_type="boundary",
        severity="P0",
        title="Maximum-size packet (MTU) processing",
        description="Send a packet at the maximum supported size (MTU). Verify no internal buffer overflow and correct end-of-packet handling.",
        trigger_signals=["packet", "frame", "length", "mtu", "jumbo", "byte_count"],
        verification_approach="uvm",
        provenance="IEEE 802.3 — Ethernet MTU",
        provenance_type="spec",
        rationale="MTU-size packet may overflow internal buffers sized for average case.",
    ),
    CornerCaseTemplate(
        id="CC-NET-003", category="network_packet", corner_type="error",
        severity="P0",
        title="Malformed packet/frame handling",
        description="Send a packet with invalid header, truncated payload, or wrong protocol version. Verify the design drops it and reports an error — not crash or hang.",
        trigger_signals=["error", "err", "drop", "invalid", "header", "parser"],
        verification_approach="uvm",
        provenance="Network DV practice",
        provenance_type="industry_practice",
        rationale="Malformed packet that isn't dropped = security vulnerability and data corruption.",
    ),
    CornerCaseTemplate(
        id="CC-NET-004", category="network_packet", corner_type="concurrent",
        severity="P0",
        title="Back-to-back packet reception",
        description="Send packets with minimum inter-packet gap (IPG). Verify no packets are lost and all are processed correctly.",
        trigger_signals=["sof", "eof", "ipg", "gap", "packet", "frame"],
        verification_approach="uvm",
        provenance="IEEE 802.3 — minimum IPG",
        provenance_type="spec",
        rationale="Minimum IPG = maximum throughput — most likely to drop packets.",
    ),
    CornerCaseTemplate(
        id="CC-NET-005", category="network_packet", corner_type="backpressure",
        severity="P0",
        title="Flow control activation/deactivation",
        description="Trigger flow control (pause frames, credits exhausted, backpressure). Verify the sender stops within the allowed time and resumes correctly.",
        trigger_signals=["pause", "flow_control", "backpressure", "credit", "xoff", "xon"],
        verification_approach="uvm",
        provenance="IEEE 802.3 — flow control",
        provenance_type="spec",
        rationale="Flow control failure = buffer overflow = packet loss.",
    ),
    CornerCaseTemplate(
        id="CC-NET-006", category="network_packet", corner_type="error",
        severity="P0",
        title="CRC/checksum error injection",
        description="Inject a packet with an incorrect CRC or checksum. Verify the receiver detects and reports the error — packet should be dropped.",
        trigger_signals=["crc", "checksum", "fcs", "crc_err", "crc_ok"],
        verification_approach="uvm",
        provenance="IEEE 802.3 — FCS checking",
        provenance_type="spec",
        rationale="Undetected CRC error = corrupted data delivered to software.",
    ),
    CornerCaseTemplate(
        id="CC-NET-007", category="network_packet", corner_type="concurrent",
        severity="P1",
        title="Priority queue scheduling fairness",
        description="Multiple priority queues with varying fill levels. Verify the scheduler serves them according to the configured policy (strict priority, weighted round-robin, etc.).",
        trigger_signals=["priority", "queue", "scheduler", "weight", "strict", "wrr"],
        verification_approach="uvm",
        provenance="Network DV practice",
        provenance_type="industry_practice",
        rationale="Scheduling unfairness starves low-priority traffic — violates QoS guarantees.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# CDC — Cross-cutting, applies when design has multi_clock trait
# ═══════════════════════════════════════════════════════════════════════════

CDC_TEMPLATES: List[CornerCaseTemplate] = [
    CornerCaseTemplate(
        id="CC-CDC-001", category="cdc", corner_type="cdc",
        severity="P0",
        title="Single-bit synchroniser metastability window",
        description="Verify that all single-bit signals crossing clock domains pass through a proper synchroniser chain (typically 2+ flip-flops).",
        trigger_signals=["sync", "synchronizer", "ff_sync", "cdc", "cross"],
        verification_approach="formal",
        provenance="CDC verification practice — Cummings SNUG papers",
        provenance_type="industry_practice",
        rationale="Missing synchroniser = metastable value propagates = random failures.",
        requires_trait="multi_clock",
    ),
    CornerCaseTemplate(
        id="CC-CDC-002", category="cdc", corner_type="cdc",
        severity="P0",
        title="Multi-bit signal coherency across clock domain",
        description="Verify multi-bit signals (data buses, addresses) crossing clock domains use proper encoding (Gray code, handshake, or FIFO) to prevent partial updates.",
        trigger_signals=["gray", "handshake", "req_ack", "data_sync", "bus_sync"],
        verification_approach="formal",
        provenance="CDC verification practice — Cummings SNUG papers",
        provenance_type="industry_practice",
        rationale="Multi-bit partial update = metastable intermediate value = random wrong data.",
        requires_trait="multi_clock",
    ),
    CornerCaseTemplate(
        id="CC-CDC-003", category="cdc", corner_type="cdc",
        severity="P0",
        title="Gray code counter wraparound at CDC boundary",
        description="Verify the Gray-coded pointer wraps correctly from max to 0 with only a single-bit change, especially at the CDC synchroniser input.",
        trigger_signals=["gray", "wr_ptr_gray", "rd_ptr_gray", "bin2gray", "gray2bin"],
        verification_approach="formal",
        provenance="Async FIFO practice — Cummings SNUG 2002",
        provenance_type="industry_practice",
        rationale="Gray code error at wrap = two bits change = metastable pointer = data corruption.",
        requires_trait="multi_clock",
    ),
    CornerCaseTemplate(
        id="CC-CDC-004", category="cdc", corner_type="cdc",
        severity="P1",
        title="Clock ratio extreme — fast:slow (10:1)",
        description="Test with a 10:1 clock frequency ratio. Verify no data loss when fast domain produces data faster than the slow domain can consume.",
        trigger_signals=["clk", "wr_clk", "rd_clk", "clk_fast", "clk_slow"],
        verification_approach="uvm",
        provenance="Async FIFO verification practice",
        provenance_type="industry_practice",
        rationale="Extreme ratios expose pointer comparison timing bugs.",
        requires_trait="multi_clock",
    ),
    CornerCaseTemplate(
        id="CC-CDC-005", category="cdc", corner_type="cdc",
        severity="P1",
        title="Clock ratio extreme — slow:fast (1:10)",
        description="Test with a 1:10 clock frequency ratio. Verify the fast side correctly waits for the slow side.",
        trigger_signals=["clk", "wr_clk", "rd_clk", "clk_fast", "clk_slow"],
        verification_approach="uvm",
        provenance="Async FIFO verification practice",
        provenance_type="industry_practice",
        rationale="Slow producer to fast consumer may cause empty flag oscillation.",
        requires_trait="multi_clock",
    ),
    CornerCaseTemplate(
        id="CC-CDC-006", category="cdc", corner_type="reset",
        severity="P0",
        title="Reset synchronisation across clock domains",
        description="Verify reset is properly synchronised when it crosses clock domains. Assert reset asynchronously, deassert synchronously in each domain.",
        trigger_signals=["rst", "reset", "rstn", "sync_rst", "async_rst", "rst_sync"],
        verification_approach="formal",
        provenance="CDC verification practice — Cummings",
        provenance_type="industry_practice",
        rationale="Unsynchronised reset deassertion across domains = one domain exits reset early.",
        requires_trait="multi_clock",
    ),
    CornerCaseTemplate(
        id="CC-CDC-007", category="cdc", corner_type="cdc",
        severity="P0",
        title="Fast-to-slow pulse capture",
        description="Drive a one-cycle pulse from a fast clock domain into a slower clock domain. Verify the design stretches, toggles, or handshakes the pulse so it is not lost.",
        trigger_signals=["pulse", "toggle", "event", "sync", "cdc", "ack"],
        verification_approach="formal",
        provenance="CDC verification practice",
        provenance_type="industry_practice",
        rationale="One-cycle fast-domain pulses are often invisible to a slow-domain sampler without explicit pulse synchronization.",
        requires_trait="multi_clock",
    ),
    CornerCaseTemplate(
        id="CC-CDC-008", category="cdc", corner_type="cdc",
        severity="P0",
        title="CDC handshake backpressure",
        description="Hold the destination side not-ready while source side keeps issuing events. Verify the CDC path applies backpressure or queues events without duplication or loss.",
        trigger_signals=["req", "ack", "ready", "valid", "full", "cdc"],
        verification_approach="uvm",
        provenance="CDC verification practice",
        provenance_type="industry_practice",
        rationale="CDC backpressure bugs appear only when clock ratio and downstream stalls combine.",
        requires_trait="multi_clock",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Aggregate + Lookup Helpers
# ═══════════════════════════════════════════════════════════════════════════

ALL_TEMPLATES: List[CornerCaseTemplate] = (
    UNIVERSAL_TEMPLATES
    + STORAGE_TEMPLATES
    + PROTOCOL_BRIDGE_TEMPLATES
    + PROCESSOR_CORE_TEMPLATES
    + PERIPHERAL_TEMPLATES
    + CRYPTO_SECURITY_TEMPLATES
    + DMA_DATA_MOVER_TEMPLATES
    + BUS_INTERCONNECT_TEMPLATES
    + MEMORY_CONTROLLER_TEMPLATES
    + DSP_DATAPATH_TEMPLATES
    + CLOCK_RESET_PMU_TEMPLATES
    + ARBITER_SCHEDULER_TEMPLATES
    + TEST_DEBUG_TEMPLATES
    + NETWORK_PACKET_TEMPLATES
    + CDC_TEMPLATES
)

_CATEGORY_INDEX: Dict[str, List[CornerCaseTemplate]] = {
    "universal":         UNIVERSAL_TEMPLATES,
    "storage":           STORAGE_TEMPLATES,
    "protocol_bridge":   PROTOCOL_BRIDGE_TEMPLATES,
    "processor_core":    PROCESSOR_CORE_TEMPLATES,
    "peripheral":        PERIPHERAL_TEMPLATES,
    "crypto_security":   CRYPTO_SECURITY_TEMPLATES,
    "dma_data_mover":    DMA_DATA_MOVER_TEMPLATES,
    "bus_interconnect":  BUS_INTERCONNECT_TEMPLATES,
    "memory_controller": MEMORY_CONTROLLER_TEMPLATES,
    "dsp_datapath":      DSP_DATAPATH_TEMPLATES,
    "clock_reset_pmu":   CLOCK_RESET_PMU_TEMPLATES,
    "arbiter_scheduler": ARBITER_SCHEDULER_TEMPLATES,
    "test_debug":        TEST_DEBUG_TEMPLATES,
    "network_packet":    NETWORK_PACKET_TEMPLATES,
    "cdc":               CDC_TEMPLATES,
}


def get_templates_for_category(category: str) -> List[CornerCaseTemplate]:
    """Return all corner case templates for a design category.

    Always includes universal templates alongside the category-specific ones.
    """
    key = category.lower().replace(" ", "_").replace("-", "_")
    category_templates = _CATEGORY_INDEX.get(key, [])
    if key == "universal":
        return list(category_templates)
    return list(UNIVERSAL_TEMPLATES) + list(category_templates)


def get_universal_templates() -> List[CornerCaseTemplate]:
    """Return templates that apply to all designs."""
    return list(UNIVERSAL_TEMPLATES)


def get_templates_for_trait(trait: str) -> List[CornerCaseTemplate]:
    """Return all templates that require a specific design trait.

    For example, ``get_templates_for_trait("multi_clock")`` returns
    all CDC templates and any other templates with ``requires_trait="multi_clock"``.
    """
    return [t for t in ALL_TEMPLATES if t.requires_trait == trait]


def get_all_templates() -> List[CornerCaseTemplate]:
    """Return every template in the knowledge base."""
    return list(ALL_TEMPLATES)
