"""
Signal-Pattern Inference Rules — Knowledge Base

These rules detect corner cases based on the *existence* of specific signal
name patterns in a design, regardless of design category.  They are the
"Layer 2" detection that fires when a signal name matches a known pattern.

Each rule defines ``trigger_patterns`` — case-insensitive substrings that
are matched against actual port and internal signal names.  If *any*
signal matches *any* pattern, the rule is considered applicable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class SignalInferenceRule:
    """A rule that infers a corner case from signal name patterns.

    If ANY signal in the design matches ANY of the ``trigger_patterns``,
    this corner case is considered applicable.

    Attributes:
        id:                     Unique identifier (e.g. ``"SIG-ERR-001"``).
        title:                  Short descriptive title.
        description:            What to test when this signal pattern is detected.
        trigger_patterns:       Signal name substrings to match (case-insensitive).
        severity:               ``"P0"`` | ``"P1"`` | ``"P2"``.
        corner_type:            Functional category of the corner case.
        verification_approach:  ``"formal"`` | ``"uvm"`` | ``"unitsim"``.
        provenance:             Source reference.
        rationale:              Why this corner case matters.
        stimulus_hint:          How to create the stimulus.
        check_hint:             What to verify.
        min_matches:            Minimum number of distinct matching signals
                                required for this rule to fire (default 1).
    """

    id: str
    title: str
    description: str
    trigger_patterns: List[str]
    severity: str
    corner_type: str
    verification_approach: str
    provenance: str
    rationale: str
    stimulus_hint: str = ""
    check_hint: str = ""
    min_matches: int = 1


# ═══════════════════════════════════════════════════════════════════════════
# Error / Fault Signals
# ═══════════════════════════════════════════════════════════════════════════

_ERROR_SIGNAL_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-ERR-001",
        title="Error signal detected — test error injection and recovery",
        description="The design has an explicit error signal. Inject conditions that trigger it, then verify the design recovers to a functional state after the error clears.",
        trigger_patterns=["error", "err", "fault", "fail"],
        severity="P0",
        corner_type="error",
        verification_approach="uvm",
        provenance="Structural inference — error signal existence",
        rationale="If the designer added an error output, it must be exercised. Un-tested error paths are the #1 source of silent failures in silicon.",
        stimulus_hint="Trigger the error condition, observe flag, then clear/recover and verify normal operation resumes.",
        check_hint="Error flag asserts within specified latency; design returns to functional state after clear.",
    ),
    SignalInferenceRule(
        id="SIG-ERR-002",
        title="Overflow signal detected — test overflow condition",
        description="The design has an explicit overflow indicator. Force the condition that triggers overflow and verify it is handled as specified (data preserved, error flagged, no hang).",
        trigger_patterns=["overflow", "ovfl", "ovf"],
        severity="P0",
        corner_type="boundary",
        verification_approach="formal",
        provenance="Structural inference — overflow signal existence",
        rationale="Overflow without protection causes data corruption in buffers and counters.",
        stimulus_hint="Fill the associated resource beyond its capacity.",
        check_hint="Overflow flag asserts; existing data is not corrupted; design does not hang.",
    ),
    SignalInferenceRule(
        id="SIG-ERR-003",
        title="Underflow signal detected — test underflow condition",
        description="The design has an explicit underflow indicator. Force the underflow condition and verify correct handling.",
        trigger_patterns=["underflow", "udfl", "udf"],
        severity="P0",
        corner_type="boundary",
        verification_approach="formal",
        provenance="Structural inference — underflow signal existence",
        rationale="Underflow read returns stale or undefined data — silent corruption.",
    ),
    SignalInferenceRule(
        id="SIG-ERR-004",
        title="Timeout signal detected — test timeout trigger conditions",
        description="The design has a timeout mechanism. Verify it triggers at the correct threshold and that the design responds appropriately (error flag, abort, reset).",
        trigger_patterns=["timeout", "tmo", "timer_exp", "watchdog"],
        severity="P1",
        corner_type="timing",
        verification_approach="uvm",
        provenance="Structural inference — timeout signal existence",
        rationale="Timeout that doesn't fire = system hangs forever on unresponsive transfers.",
    ),
    SignalInferenceRule(
        id="SIG-ERR-005",
        title="Parity/ECC/CRC signal detected — test error detection",
        description="The design includes error detection logic. Inject single-bit and multi-bit errors and verify detection and optional correction.",
        trigger_patterns=["parity", "ecc", "checksum", "crc"],
        severity="P0",
        corner_type="error",
        verification_approach="unitsim",
        provenance="Structural inference — error detection signal existence",
        rationale="Error detection logic that doesn't detect = worse than not having it (false confidence).",
        stimulus_hint="Inject known-bad data; verify error flag asserts and (if ECC) correction is applied.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Flow Control Signals
# ═══════════════════════════════════════════════════════════════════════════

_FLOW_CONTROL_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-FLOW-001",
        title="Full signal detected — test write when full",
        description="The design has a full indicator. Attempt to write/push when full is asserted. Verify the write is rejected or handled per spec.",
        trigger_patterns=["full", "fifo_full", "tx_full"],
        severity="P0",
        corner_type="boundary",
        verification_approach="formal",
        provenance="Structural inference — full flag existence",
        rationale="Write when full without protection = data overwrite = corruption.",
    ),
    SignalInferenceRule(
        id="SIG-FLOW-002",
        title="Empty signal detected — test read when empty",
        description="The design has an empty indicator. Attempt to read/pop when empty is asserted. Verify the read is rejected or returns defined value.",
        trigger_patterns=["empty", "fifo_empty", "rx_empty"],
        severity="P0",
        corner_type="boundary",
        verification_approach="formal",
        provenance="Structural inference — empty flag existence",
        rationale="Read when empty returns stale data or X — silent corruption.",
    ),
    SignalInferenceRule(
        id="SIG-FLOW-003",
        title="Almost-full threshold detected — test watermark behaviour",
        description="The design has a threshold/watermark indicator. Verify it asserts at the correct fill level.",
        trigger_patterns=["almost_full", "afull", "nearly_full", "watermark"],
        severity="P1",
        corner_type="boundary",
        verification_approach="uvm",
        provenance="OpenTitan UART DV plan — watermark testing",
        rationale="Wrong watermark threshold = flow control kicks in too early or too late.",
    ),
    SignalInferenceRule(
        id="SIG-FLOW-004",
        title="Almost-empty threshold detected — test drain threshold",
        description="The design has a near-empty indicator. Verify it asserts at the correct level.",
        trigger_patterns=["almost_empty", "aempty", "nearly_empty"],
        severity="P1",
        corner_type="boundary",
        verification_approach="uvm",
        provenance="Structural inference — near-empty flag existence",
        rationale="Wrong drain threshold = downstream starved or early spurious interrupt.",
    ),
    SignalInferenceRule(
        id="SIG-FLOW-005",
        title="Valid-ready pair detected — test backpressure",
        description="The design has both valid and ready signals, indicating a handshake interface. Test sustained backpressure (ready low while valid high).",
        trigger_patterns=["ready", "valid"],
        severity="P0",
        corner_type="backpressure",
        verification_approach="uvm",
        provenance="Industry standard handshake pattern",
        rationale="Backpressure handling errors are the most common handshake bug.",
        stimulus_hint="Assert valid, hold ready low for many cycles, then release.",
        min_matches=2,
    ),
    SignalInferenceRule(
        id="SIG-FLOW-006",
        title="Stall signal detected — test stall + new request interaction",
        description="The design has a stall/wait/busy signal. Verify behaviour when a new request arrives during stall — should be queued, rejected, or cause error.",
        trigger_patterns=["stall", "wait", "busy", "hold"],
        severity="P1",
        corner_type="concurrent",
        verification_approach="uvm",
        provenance="Structural inference — stall signal existence",
        rationale="New request during stall that is silently dropped = lost transaction.",
    ),
    SignalInferenceRule(
        id="SIG-FLOW-007",
        title="Credit signal detected — test credit exhaustion",
        description="The design uses credit-based flow control. Exhaust all credits and verify the sender stops. Then restore credits and verify transmission resumes.",
        trigger_patterns=["credit", "token"],
        severity="P1",
        corner_type="backpressure",
        verification_approach="uvm",
        provenance="Structural inference — credit flow control",
        rationale="Sending without credits = buffer overflow at receiver.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Arbitration Signals
# ═══════════════════════════════════════════════════════════════════════════

_ARBITRATION_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-ARB-001",
        title="Grant signal detected — test grant fairness",
        description="The design has an arbiter grant output. Verify that all requestors eventually receive grants under sustained contention.",
        trigger_patterns=["grant", "gnt"],
        severity="P0",
        corner_type="concurrent",
        verification_approach="formal",
        provenance="Structural inference — arbiter grant existence",
        rationale="Unfair grant = starvation = subsystem hang.",
    ),
    SignalInferenceRule(
        id="SIG-ARB-002",
        title="Request signal detected — test all-request scenario",
        description="The design has request inputs. Assert all requests simultaneously and verify exactly one grant is issued.",
        trigger_patterns=["request", "req"],
        severity="P0",
        corner_type="concurrent",
        verification_approach="uvm",
        provenance="Structural inference — arbiter request existence",
        rationale="Multiple grants = bus contention = electrical damage or data corruption.",
    ),
    SignalInferenceRule(
        id="SIG-ARB-003",
        title="Priority signal detected — test priority inversion",
        description="The design has priority inputs. Verify a low-priority holder does not block a high-priority requestor indefinitely.",
        trigger_patterns=["priority", "pri", "qos"],
        severity="P1",
        corner_type="concurrent",
        verification_approach="formal",
        provenance="Structural inference — priority signal existence",
        rationale="Priority inversion violates real-time guarantees.",
    ),
    SignalInferenceRule(
        id="SIG-ARB-004",
        title="Lock signal detected — test lock/unlock sequence",
        description="The design has a lock mechanism. Verify locked access prevents re-arbitration and that unlock releases correctly.",
        trigger_patterns=["lock", "locked", "exclusive"],
        severity="P1",
        corner_type="protocol",
        verification_approach="uvm",
        provenance="ARM AMBA spec — locked access",
        rationale="Broken lock = atomic operations fail = software race conditions.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Interrupt Signals
# ═══════════════════════════════════════════════════════════════════════════

_INTERRUPT_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-INT-001",
        title="Interrupt output detected — test interrupt assertion conditions",
        description="The design has an interrupt output. Trigger the interrupt condition and verify the IRQ asserts with correct timing.",
        trigger_patterns=["irq", "interrupt", "intr"],
        severity="P0",
        corner_type="concurrent",
        verification_approach="unitsim",
        provenance="Peripheral DV practice",
        rationale="Un-tested interrupt path = interrupt never fires in real system = silent feature failure.",
        check_hint="IRQ asserts within specified latency of the triggering event.",
    ),
    SignalInferenceRule(
        id="SIG-INT-002",
        title="Interrupt mask detected — test masked interrupt behaviour",
        description="The design has interrupt mask/enable bits. Set the mask, trigger the event, and verify the interrupt does NOT fire but the pending/status bit IS set.",
        trigger_patterns=["mask", "imask", "ien", "int_en"],
        severity="P1",
        corner_type="concurrent",
        verification_approach="unitsim",
        provenance="OpenTitan peripheral DV methodology",
        rationale="Masked interrupt that clears pending = lost interrupt when unmasked.",
    ),
    SignalInferenceRule(
        id="SIG-INT-003",
        title="Interrupt clear detected — test clear timing race",
        description="The design has interrupt clear/acknowledge. Clear the interrupt at the exact same cycle a new event occurs. Verify the new event is not lost.",
        trigger_patterns=["int_clr", "irq_clr", "iclr", "int_ack"],
        severity="P1",
        corner_type="concurrent",
        verification_approach="formal",
        provenance="Peripheral DV practice",
        rationale="Clear + new event race = lost interrupt = system hang.",
    ),
    SignalInferenceRule(
        id="SIG-INT-004",
        title="Interrupt pending detected — test pending while masked",
        description="The design has interrupt pending/status bits. Verify pending is set even when the interrupt is masked, and fires when the mask clears.",
        trigger_patterns=["pending", "int_status", "irq_status"],
        severity="P1",
        corner_type="status",
        verification_approach="unitsim",
        provenance="Peripheral DV practice",
        rationale="Pending bit that doesn't persist = software polls status and never sees the event.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Mode / Configuration Signals
# ═══════════════════════════════════════════════════════════════════════════

_CONFIG_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-CFG-001",
        title="Mode select detected — test mode switching during operation",
        description="The design has a mode select input. Change the mode while the design is actively processing. Verify graceful transition.",
        trigger_patterns=["mode", "cfg_mode", "opmode"],
        severity="P0",
        corner_type="config",
        verification_approach="uvm",
        provenance="Peripheral DV practice",
        rationale="Hot mode switch mid-operation causes undefined output.",
    ),
    SignalInferenceRule(
        id="SIG-CFG-002",
        title="Enable signal detected — test enable/disable during active",
        description="The design has an enable/start/go control. Toggle it during active operation. Verify the design stops cleanly and can be re-enabled.",
        trigger_patterns=["enable", "en", "start", "go"],
        severity="P0",
        corner_type="config",
        verification_approach="uvm",
        provenance="Peripheral DV practice",
        rationale="Disable during active that doesn't clean up = re-enable sees corrupt state.",
    ),
    SignalInferenceRule(
        id="SIG-CFG-003",
        title="Bypass signal detected — test bypass path correctness",
        description="The design has a bypass or passthrough mode. Enable bypass and verify data passes through unmodified.",
        trigger_patterns=["bypass", "passthrough"],
        severity="P1",
        corner_type="config",
        verification_approach="unitsim",
        provenance="Structural inference — bypass signal existence",
        rationale="Bypass path that modifies data = wrong data in debug/test mode.",
    ),
    SignalInferenceRule(
        id="SIG-CFG-004",
        title="Loopback signal detected — test loopback path",
        description="The design has loopback mode. Enable loopback and verify transmitted data is received back correctly.",
        trigger_patterns=["loopback", "loop"],
        severity="P1",
        corner_type="config",
        verification_approach="unitsim",
        provenance="OpenTitan UART DV plan — loopback test",
        rationale="Loopback that doesn't work = primary self-test mechanism is broken.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Power Management Signals
# ═══════════════════════════════════════════════════════════════════════════

_POWER_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-PWR-001",
        title="Sleep/wakeup detected — test power state transition timing",
        description="The design has sleep/wakeup controls. Verify transition timing, data preservation, and clean resume.",
        trigger_patterns=["sleep", "wakeup", "power_down", "standby"],
        severity="P1",
        corner_type="power",
        verification_approach="uvm",
        provenance="UPF/CPF power-aware verification practice",
        rationale="Sleep transition that loses state = data corruption on wakeup.",
    ),
    SignalInferenceRule(
        id="SIG-PWR-002",
        title="Retention signals detected — test register retention",
        description="The design has retention save/restore controls. Verify all retained registers have correct values after power cycle.",
        trigger_patterns=["retention", "retain", "save", "restore"],
        severity="P1",
        corner_type="power",
        verification_approach="uvm",
        provenance="UPF/CPF power-aware verification practice",
        rationale="Failed retention = full re-initialisation required = boot time regression.",
    ),
    SignalInferenceRule(
        id="SIG-PWR-003",
        title="Isolation signals detected — test isolation cell behaviour",
        description="The design has isolation/clamp controls. Verify isolation activates before power removal and deactivates after power stable.",
        trigger_patterns=["isolate", "isolation", "clamp"],
        severity="P1",
        corner_type="power",
        verification_approach="formal",
        provenance="UPF/CPF power-aware verification practice",
        rationale="Late isolation = floating outputs drive X into active domain.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Counter / Address / Pointer Signals
# ═══════════════════════════════════════════════════════════════════════════

_ADDRESS_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-ADDR-001",
        title="Address signal detected — test boundary addresses",
        description="The design has address inputs. Test with address = 0 (minimum) and address = max (all ones). Verify no wraparound or decode error.",
        trigger_patterns=["addr", "address"],
        severity="P1",
        corner_type="boundary",
        verification_approach="unitsim",
        provenance="Structural inference — address signal existence",
        rationale="Address boundary bugs cause access to wrong memory location.",
    ),
    SignalInferenceRule(
        id="SIG-ADDR-002",
        title="Counter signal detected — test rollover/wraparound",
        description="The design has a counter. Run it until it reaches its maximum value and wraps. Verify correct wraparound behaviour.",
        trigger_patterns=["count", "counter", "cnt"],
        severity="P0",
        corner_type="boundary",
        verification_approach="formal",
        provenance="Structural inference — counter existence",
        rationale="Counter overflow that wraps incorrectly = wrong count = wrong behaviour downstream.",
    ),
    SignalInferenceRule(
        id="SIG-ADDR-003",
        title="Pointer signal detected — test pointer collision",
        description="The design has read/write pointers. Test when they collide (equal) — this typically indicates full or empty. Verify flag logic.",
        trigger_patterns=["ptr", "pointer", "rd_ptr", "wr_ptr"],
        severity="P0",
        corner_type="boundary",
        verification_approach="formal",
        provenance="FIFO verification practice",
        rationale="Pointer collision with wrong flag = overflow/underflow not detected.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Data Integrity Signals
# ═══════════════════════════════════════════════════════════════════════════

_DATA_INTEGRITY_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-DATA-001",
        title="Data valid signal detected — test data stability while valid",
        description="The design has a data_valid indicator. Verify that data does not change while data_valid is asserted.",
        trigger_patterns=["data_valid", "dvalid", "d_valid"],
        severity="P0",
        corner_type="stability",
        verification_approach="formal",
        provenance="Handshake protocol practice",
        rationale="Unstable data during valid = downstream samples wrong value.",
    ),
    SignalInferenceRule(
        id="SIG-DATA-002",
        title="Write strobe detected — test partial write correctness",
        description="The design has write byte enables/strobes. Verify that only the byte lanes indicated by the strobe are modified; other bytes must be preserved.",
        trigger_patterns=["strobe", "strb", "byte_en", "wstrb"],
        severity="P1",
        corner_type="boundary",
        verification_approach="uvm",
        provenance="ARM AMBA spec — WSTRB",
        rationale="Wrong strobe handling corrupts adjacent bytes in the same word.",
    ),
    SignalInferenceRule(
        id="SIG-DATA-003",
        title="Tag/hit signal detected — test tag matching logic",
        description="The design has cache-like tag matching. Verify hit/miss detection with known tag values including aliased and boundary addresses.",
        trigger_patterns=["tag", "tag_match", "hit", "miss"],
        severity="P1",
        corner_type="boundary",
        verification_approach="formal",
        provenance="Cache verification practice",
        rationale="Tag match bug = cache hit on wrong data = silent data corruption.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# DMA / Data Mover Signals
# ═══════════════════════════════════════════════════════════════════════════

_DMA_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-DMA-001",
        title="DMA channel detected — test channel collision",
        description="The design has DMA channels. If two channels target the same memory region simultaneously, verify arbitration is correct and data is not interleaved.",
        trigger_patterns=["dma", "channel", "ch_sel"],
        severity="P0", corner_type="concurrent", verification_approach="uvm",
        provenance="DMA verification practice",
        rationale="Two DMA channels writing same region without arbitration = data interleaving.",
    ),
    SignalInferenceRule(
        id="SIG-DMA-002",
        title="DMA burst signal detected — test unaligned burst",
        description="The design has DMA burst transfers. Test with source/destination addresses not aligned to burst size boundary.",
        trigger_patterns=["dma_burst", "burst_len", "burst_size"],
        severity="P0", corner_type="boundary", verification_approach="uvm",
        provenance="DMA verification practice",
        rationale="Unaligned DMA burst may cross page boundary or wrap incorrectly.",
    ),
    SignalInferenceRule(
        id="SIG-DMA-003",
        title="DMA done/complete signal detected — test completion with error",
        description="The design has a DMA completion indicator. Verify it asserts on both successful completion and error termination.",
        trigger_patterns=["dma_done", "dma_complete", "xfer_done"],
        severity="P0", corner_type="status", verification_approach="uvm",
        provenance="DMA verification practice",
        rationale="Missing done on error = software waits forever for transfer that already failed.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Cache / Memory Hierarchy Signals
# ═══════════════════════════════════════════════════════════════════════════

_CACHE_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-CACHE-001",
        title="Cache flush/invalidate signal detected — test flush during active",
        description="The design has cache control signals. Flush the cache while a read/write is in progress. Verify data consistency.",
        trigger_patterns=["cache_flush", "cache_inv", "invalidate"],
        severity="P0", corner_type="concurrent", verification_approach="uvm",
        provenance="Cache verification practice",
        rationale="Flush during active linefill = partial data in cache line.",
    ),
    SignalInferenceRule(
        id="SIG-CACHE-002",
        title="Cache dirty/modified signal detected — test writeback correctness",
        description="The design tracks dirty cache lines. Verify dirty lines are written back to memory before eviction or invalidation.",
        trigger_patterns=["dirty", "modified", "writeback"],
        severity="P0", corner_type="data_integrity", verification_approach="uvm",
        provenance="Cache verification practice",
        rationale="Lost dirty data on eviction = memory has stale value = silent data corruption.",
    ),
    SignalInferenceRule(
        id="SIG-CACHE-003",
        title="Cache coherence signal detected — test snoop response",
        description="The design has coherence/snoop signals. Verify correct snoop response (shared, modified, invalid) for each cache state.",
        trigger_patterns=["snoop", "coherent", "share", "mesi", "moesi"],
        severity="P0", corner_type="protocol", verification_approach="uvm",
        provenance="Cache coherence verification practice",
        rationale="Wrong snoop response = multiple copies of modified data = coherence violation.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline / Hazard Signals
# ═══════════════════════════════════════════════════════════════════════════

_PIPELINE_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-PIPE-001",
        title="Forwarding/bypass signal detected — test forwarding correctness",
        description="The design has data forwarding paths. Test back-to-back dependent instructions to verify forwarding provides correct data.",
        trigger_patterns=["forward", "bypass_data", "hazard"],
        severity="P0", corner_type="hazard", verification_approach="uvm",
        provenance="Patterson & Hennessy pipeline hazard methodology",
        rationale="Wrong forwarding = instruction uses stale register value = wrong computation.",
    ),
    SignalInferenceRule(
        id="SIG-PIPE-002",
        title="Pipeline flush signal detected — test flush + refetch",
        description="The design has a pipeline flush mechanism. Trigger flush and verify all in-flight instructions are correctly cancelled and execution resumes at the correct address.",
        trigger_patterns=["flush", "pipeline_flush", "squash", "kill"],
        severity="P0", corner_type="hazard", verification_approach="uvm",
        provenance="Processor pipeline verification practice",
        rationale="Incomplete flush = cancelled instruction's side effects commit = wrong state.",
    ),
    SignalInferenceRule(
        id="SIG-PIPE-003",
        title="Branch predict signal detected — test misprediction recovery",
        description="The design has branch prediction. Force a misprediction and verify the pipeline recovers correctly with no architectural side effects from the wrong path.",
        trigger_patterns=["predict", "branch_taken", "btb", "bht", "mispredict"],
        severity="P0", corner_type="hazard", verification_approach="uvm",
        provenance="Processor pipeline verification practice",
        rationale="Misprediction recovery that leaves wrong-path side effects = state corruption.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Clock Domain Crossing Signals
# ═══════════════════════════════════════════════════════════════════════════

_CDC_SIGNAL_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-CDC-001",
        title="Synchronizer detected — test metastability window",
        description="The design has multi-flop synchronizers. Verify the synchronized signal settles to a valid value within the synchronization latency.",
        trigger_patterns=["sync", "synchronizer", "sync_ff", "cdc_sync"],
        severity="P0", corner_type="cdc", verification_approach="formal",
        provenance="Cummings SNUG CDC methodology",
        rationale="Insufficient synchronization stages = metastability propagates into logic.",
    ),
    SignalInferenceRule(
        id="SIG-CDC-002",
        title="Gray code signal detected — test Gray-to-binary conversion",
        description="The design uses Gray coding (likely for CDC pointer crossing). Verify the Gray-to-binary and binary-to-Gray conversions are correct at all values.",
        trigger_patterns=["gray", "grey", "gray_code", "bin2gray"],
        severity="P0", corner_type="cdc", verification_approach="formal",
        provenance="Cummings SNUG async FIFO methodology",
        rationale="Gray code conversion error = wrong pointer value in receiving domain = flag error.",
    ),
    SignalInferenceRule(
        id="SIG-CDC-003",
        title="Handshake CDC signal detected — test handshake across domains",
        description="The design has a req/ack handshake crossing clock domains. Verify no data is lost even when clock ratio changes dynamically.",
        trigger_patterns=["cdc_req", "cdc_ack", "async_req", "async_ack"],
        severity="P0", corner_type="cdc", verification_approach="uvm",
        provenance="Cummings SNUG CDC methodology",
        rationale="Broken CDC handshake = data transfer glitch or lost pulse.",
    ),
    SignalInferenceRule(
        id="SIG-CDC-004",
        title="Pulse synchronizer detected — test back-to-back pulses",
        description="The design has a pulse synchronizer. Send two pulses close together (within synchronization latency). Verify both are transferred or the design gracefully handles pulse merging.",
        trigger_patterns=["pulse_sync", "toggle_sync", "pulse_cdc"],
        severity="P0", corner_type="cdc", verification_approach="uvm",
        provenance="Cummings SNUG CDC methodology",
        rationale="Fast-to-slow pulse sync may lose the second pulse if sent before the first is acknowledged.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Serial Interface Signals
# ═══════════════════════════════════════════════════════════════════════════

_SERIAL_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-SER-001",
        title="FIFO threshold signal detected — test threshold at all levels",
        description="The design has configurable FIFO thresholds. Test with threshold at minimum (1), maximum (depth-1), and equal to depth.",
        trigger_patterns=["threshold", "fifo_lvl", "fill_level", "wm_lvl"],
        severity="P1", corner_type="boundary", verification_approach="uvm",
        provenance="Peripheral DV practice",
        rationale="Threshold at extreme values may exhibit off-by-one in comparison logic.",
    ),
    SignalInferenceRule(
        id="SIG-SER-002",
        title="Chip select / slave select detected — test multi-slave selection",
        description="The design has chip/slave select outputs. Verify exactly one CS is active at a time and that CS timing meets setup/hold.",
        trigger_patterns=["cs", "csn", "ss", "chip_sel", "slave_sel"],
        severity="P0", corner_type="protocol", verification_approach="uvm",
        provenance="SPI protocol verification practice",
        rationale="Multiple CS active = bus contention on shared MISO line.",
    ),
    SignalInferenceRule(
        id="SIG-SER-003",
        title="Baud rate / clock divider signal detected — test extreme rates",
        description="The design has a configurable clock divider. Test with divider=1 (maximum rate) and divider=max (minimum rate).",
        trigger_patterns=["baud", "divider", "prescaler", "nco"],
        severity="P1", corner_type="boundary", verification_approach="uvm",
        provenance="Peripheral DV practice",
        rationale="Extreme divider values may cause off-by-one in clock generation or zero-division.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Security Signals
# ═══════════════════════════════════════════════════════════════════════════

_SECURITY_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-SEC-001",
        title="Access control / firewall signal detected — test policy bypass",
        description="The design has access control signals. Attempt access from an unauthorized master. Verify the access is blocked.",
        trigger_patterns=["firewall", "access_ctrl", "permission", "protect"],
        severity="P0", corner_type="security", verification_approach="uvm",
        provenance="CWE hardware weakness patterns",
        rationale="Access control that can be bypassed = security mechanism is ineffective.",
    ),
    SignalInferenceRule(
        id="SIG-SEC-002",
        title="Scramble / encrypt signal detected — test scramble key change",
        description="The design has data scrambling. Change the scramble key and verify previously-scrambled data is no longer readable.",
        trigger_patterns=["scramble", "encrypt", "decrypt", "cipher"],
        severity="P0", corner_type="security", verification_approach="uvm",
        provenance="Memory scrambling verification practice",
        rationale="Scramble key change that doesn't invalidate old data = security gap.",
    ),
    SignalInferenceRule(
        id="SIG-SEC-003",
        title="Lifecycle / fuse signal detected — test lifecycle transition",
        description="The design has lifecycle state signals. Verify correct feature enablement/disablement at each lifecycle stage (DEV→PROD→RMA).",
        trigger_patterns=["lifecycle", "lc_state", "fuse", "otp"],
        severity="P0", corner_type="security", verification_approach="uvm",
        provenance="OpenTitan lifecycle controller methodology",
        rationale="Wrong lifecycle policy = debug enabled in production = security breach.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Aggregate list + Lookup helpers
# ═══════════════════════════════════════════════════════════════════════════

_SOC_SIGNAL_RULES: List[SignalInferenceRule] = [
    SignalInferenceRule(
        id="SIG-SOC-001",
        title="Fatal alert signal detected - test escalation path",
        description="The design has fatal/alert escalation signals. Trigger the fatal condition and verify escalation, sticky status, and recovery/reset policy.",
        trigger_patterns=["fatal", "alert", "escalate", "panic", "nmi"],
        severity="P0",
        corner_type="error",
        verification_approach="uvm",
        provenance="SoC alert/error handling verification practice",
        rationale="Fatal paths are rarely exercised by normal traffic but must work when silicon enters an unsafe state.",
        stimulus_hint="Inject the fatal condition or force the monitored error source.",
        check_hint="Alert/fatal signal asserts, status is sticky, and escalation follows the documented sequence.",
    ),
    SignalInferenceRule(
        id="SIG-SOC-002",
        title="Write-one-clear signal detected - test clear/set race",
        description="The design has clearable status or W1C-like signals. Clear the bit on the same cycle the hardware event sets it again.",
        trigger_patterns=["w1c", "clear", "clr", "status", "pending", "event"],
        severity="P1",
        corner_type="register",
        verification_approach="formal",
        provenance="CSR verification practice",
        rationale="Clear/set races are a common cause of lost interrupts and missed firmware-visible events.",
        check_hint="New hardware events remain visible after the software clear operation.",
    ),
    SignalInferenceRule(
        id="SIG-SOC-003",
        title="Write-protect or lock signal detected - test locked writes",
        description="The design has lock/protect controls. Attempt writes before and after the lock is asserted and verify locked fields cannot change.",
        trigger_patterns=["lock", "locked", "protect", "write_protect", "wp", "ro_after_lock"],
        severity="P0",
        corner_type="security",
        verification_approach="uvm",
        provenance="CSR/security verification practice",
        rationale="Lock bits that do not lock allow post-boot mutation of security or safety configuration.",
    ),
    SignalInferenceRule(
        id="SIG-SOC-004",
        title="Clock gate enable signal detected - test glitch-free gating",
        description="The design exposes clock-gate enable controls. Toggle the enable near clock boundaries and verify no extra or missing gated-clock edge is produced.",
        trigger_patterns=["clk_en", "cg_en", "clock_gate", "gated_clk", "icg"],
        severity="P0",
        corner_type="timing",
        verification_approach="formal",
        provenance="Clock gating verification practice",
        rationale="Clock-gate glitches can create state corruption that is invisible in untimed unit tests.",
    ),
    SignalInferenceRule(
        id="SIG-SOC-005",
        title="Debug or scan signal detected - test production lockout",
        description="The design has debug, scan, or test-access controls. Verify production/security lock settings disable privileged access paths.",
        trigger_patterns=["debug", "dbg", "scan", "test_mode", "jtag", "dft"],
        severity="P0",
        corner_type="security",
        verification_approach="uvm",
        provenance="Hardware security verification practice",
        rationale="Debug access left open in production is a high-impact security escape.",
    ),
    SignalInferenceRule(
        id="SIG-SOC-006",
        title="Atomic/read-modify-write signal detected - test interrupt and contention",
        description="The design includes atomic, lock, or read-modify-write controls. Verify atomicity under contention, reset, and interrupt/error conditions.",
        trigger_patterns=["atomic", "amo", "rmw", "lock", "exclusive", "cmpxchg"],
        severity="P0",
        corner_type="ordering",
        verification_approach="formal",
        provenance="Processor/interconnect verification practice",
        rationale="Broken atomicity creates software races even when the instruction or bus protocol appears to succeed.",
    ),
]

SIGNAL_RULES: List[SignalInferenceRule] = (
    _ERROR_SIGNAL_RULES
    + _FLOW_CONTROL_RULES
    + _ARBITRATION_RULES
    + _INTERRUPT_RULES
    + _CONFIG_RULES
    + _POWER_RULES
    + _ADDRESS_RULES
    + _DATA_INTEGRITY_RULES
    + _DMA_RULES
    + _CACHE_RULES
    + _PIPELINE_RULES
    + _CDC_SIGNAL_RULES
    + _SERIAL_RULES
    + _SECURITY_RULES
    + _SOC_SIGNAL_RULES
)


def get_all_signal_rules() -> List[SignalInferenceRule]:
    """Return every signal inference rule in the knowledge base."""
    return list(SIGNAL_RULES)


def match_signal_rules(
    signal_names: List[str],
) -> List[Tuple[SignalInferenceRule, List[str]]]:
    """Match signal inference rules against actual signal names.

    For each rule whose ``trigger_patterns`` match at least
    ``min_matches`` signals in *signal_names*, returns a tuple of
    ``(rule, matched_signals)``.

    Matching is case-insensitive substring matching.

    Args:
        signal_names: List of actual port/signal names from the design.

    Returns:
        List of ``(rule, matched_signal_names)`` tuples for all
        applicable rules, sorted by severity (P0 first).
    """
    lower_signals = [(s, s.lower()) for s in signal_names]
    results: List[Tuple[SignalInferenceRule, List[str]]] = []

    for rule in SIGNAL_RULES:
        matched: List[str] = []
        for original, lower in lower_signals:
            for pattern in rule.trigger_patterns:
                if pattern.lower() in lower:
                    matched.append(original)
                    break  # one match per signal is enough

        if len(matched) >= rule.min_matches:
            results.append((rule, matched))

    # Sort: P0 before P1 before P2
    severity_order = {"P0": 0, "P1": 1, "P2": 2}
    results.sort(key=lambda t: severity_order.get(t[0].severity, 9))
    return results
