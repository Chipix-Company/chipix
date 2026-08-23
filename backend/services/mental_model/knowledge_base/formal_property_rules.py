"""
Formal Property Library Rules — Knowledge Base

Generic assertion patterns derived from well-known formal verification
libraries and methodologies. These are protocol-independent and apply
broadly based on structural patterns detected in any design.

Sources:
  - Accellera Open Verification Library (OVL) — patterns, not code
  - Questa Formal ABV methodology patterns
  - SymbiYosys formal verification patterns
  - Industry-standard assertion-based verification (ABV) best practices
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class FormalPropertyRule:
    """A generic formal verification property pattern.

    Attributes:
        id:              Unique identifier.
        library_source:  Source library/methodology.
        property_class:  Category of formal property.
        title:           Short title.
        description:     What this property verifies.
        sva_template:    SVA template with ``{signal}`` placeholders.
        trigger_patterns:Signal patterns for applicability matching.
        severity:        Priority level.
        property_type:   ``"assert"`` | ``"assume"`` | ``"cover"``.
        rationale:       Why this property is important.
    """
    id: str
    library_source: str
    property_class: str
    title: str
    description: str
    sva_template: str
    trigger_patterns: List[str]
    severity: str
    property_type: str
    rationale: str


# ═══════════════════════════════════════════════════════════════════════
# FIFO / Buffer Properties
# ═══════════════════════════════════════════════════════════════════════

_FIFO_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-FIFO-001", library_source="OVL pattern: ovl_fifo_index",
        property_class="data_integrity",
        title="FIFO pointer never exceeds depth",
        description="Write pointer and read pointer must always be less than FIFO depth.",
        sva_template="assert property (@(posedge {clk}) {wr_ptr} < {DEPTH} && {rd_ptr} < {DEPTH});",
        trigger_patterns=["wr_ptr", "rd_ptr", "fifo", "depth"],
        severity="P0", property_type="assert",
        rationale="Pointer exceeding depth corrupts addressing — reads/writes go to wrong location.",
    ),
    FormalPropertyRule(
        id="FP-FIFO-002", library_source="OVL pattern: ovl_fifo_index",
        property_class="flag_correctness",
        title="Full flag only when count equals depth",
        description="Full flag must be asserted if and only if the element count equals the FIFO depth.",
        sva_template="assert property (@(posedge {clk}) {full} == ({count} == {DEPTH}));",
        trigger_patterns=["full", "count", "depth"],
        severity="P0", property_type="assert",
        rationale="Incorrect full flag = either overflow (false low) or reduced throughput (false high).",
    ),
    FormalPropertyRule(
        id="FP-FIFO-003", library_source="OVL pattern: ovl_fifo_index",
        property_class="flag_correctness",
        title="Empty flag only when count is zero",
        description="Empty flag must be asserted if and only if the element count is zero.",
        sva_template="assert property (@(posedge {clk}) {empty} == ({count} == 0));",
        trigger_patterns=["empty", "count"],
        severity="P0", property_type="assert",
        rationale="Incorrect empty flag = either underflow (false low) or stalled reads (false high).",
    ),
    FormalPropertyRule(
        id="FP-FIFO-004", library_source="Formal best practice",
        property_class="data_integrity",
        title="FIFO count monotonic with push/pop",
        description="Count increments by 1 on push-only cycle, decrements by 1 on pop-only cycle, stays same on simultaneous push+pop or idle.",
        sva_template=(
            "assert property (@(posedge {clk}) disable iff (!{rstn}) "
            "{push} && !{pop} |=> {count} == $past({count}) + 1);"
        ),
        trigger_patterns=["push", "pop", "count", "wr_en", "rd_en"],
        severity="P0", property_type="assert",
        rationale="Count drift means pointer logic has a bug — leads to flag errors.",
    ),
    FormalPropertyRule(
        id="FP-FIFO-005", library_source="Formal best practice",
        property_class="data_integrity",
        title="No push when full (unless pop)",
        description="A write/push must not be accepted when the FIFO is full, unless a simultaneous read/pop frees space.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{full} && !{pop} |-> !{push_accepted});"
        ),
        trigger_patterns=["full", "push", "pop", "wr_en", "rd_en"],
        severity="P0", property_type="assert",
        rationale="Accepting push when truly full = data overwrite.",
    ),
    FormalPropertyRule(
        id="FP-FIFO-006", library_source="Formal best practice",
        property_class="data_integrity",
        title="No pop when empty (unless push)",
        description="A read/pop must not produce valid data when the FIFO is empty, unless a simultaneous write/push provides data.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{empty} && !{push} |-> !{pop_valid});"
        ),
        trigger_patterns=["empty", "push", "pop", "wr_en", "rd_en"],
        severity="P0", property_type="assert",
        rationale="Pop from empty returns stale/garbage data.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Counter Properties
# ═══════════════════════════════════════════════════════════════════════

_COUNTER_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-CNT-001", library_source="OVL pattern: ovl_range",
        property_class="range_check",
        title="Counter within valid range",
        description="Counter value must remain within [0, MAX_VALUE] at all times.",
        sva_template="assert property (@(posedge {clk}) {counter} <= {MAX_VALUE});",
        trigger_patterns=["counter", "count", "cnt"],
        severity="P0", property_type="assert",
        rationale="Counter exceeding range indicates overflow bug.",
    ),
    FormalPropertyRule(
        id="FP-CNT-002", library_source="OVL pattern: ovl_increment",
        property_class="data_integrity",
        title="Counter increments by exactly 1",
        description="When enabled, counter value must increase by exactly 1 per clock cycle.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{enable} && !{clear} |=> {counter} == $past({counter}) + 1);"
        ),
        trigger_patterns=["counter", "count", "enable", "increment"],
        severity="P1", property_type="assert",
        rationale="Counter that skips values or increments by wrong amount corrupts downstream logic.",
    ),
    FormalPropertyRule(
        id="FP-CNT-003", library_source="OVL pattern: ovl_cycle_sequence",
        property_class="timing",
        title="Counter clears to zero on clear signal",
        description="When clear/reset is asserted, counter must go to zero on the next clock edge.",
        sva_template="assert property (@(posedge {clk}) {clear} |=> {counter} == 0);",
        trigger_patterns=["counter", "clear", "reset"],
        severity="P0", property_type="assert",
        rationale="Counter that doesn't clear on reset retains stale count — affects all downstream logic.",
    ),
    FormalPropertyRule(
        id="FP-CNT-004", library_source="Formal best practice",
        property_class="range_check",
        title="Saturating counter does not wrap",
        description="If the counter is designed to saturate, verify it stays at MAX_VALUE when incremented at maximum.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{counter} == {MAX_VALUE} && {enable} |=> {counter} == {MAX_VALUE});"
        ),
        trigger_patterns=["saturate", "counter", "max"],
        severity="P0", property_type="assert",
        rationale="Counter designed to saturate but wrapping instead causes magnitude-scale errors.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# FSM Properties
# ═══════════════════════════════════════════════════════════════════════

_FSM_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-FSM-001", library_source="OVL pattern: ovl_next",
        property_class="state_machine",
        title="FSM has no dead states (every state is reachable)",
        description="Formally verify that every defined FSM state is reachable from the reset state via some legal input sequence.",
        sva_template="cover property (@(posedge {clk}) {state} == {TARGET_STATE});",
        trigger_patterns=["state", "fsm", "next_state"],
        severity="P1", property_type="cover",
        rationale="Unreachable states indicate dead code or over-specified FSM.",
    ),
    FormalPropertyRule(
        id="FP-FSM-002", library_source="OVL pattern: ovl_never",
        property_class="state_machine",
        title="FSM never enters invalid state",
        description="Verify the FSM state register never holds a value outside the set of defined states.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{state} inside {{{VALID_STATES}}});"
        ),
        trigger_patterns=["state", "fsm", "next_state"],
        severity="P0", property_type="assert",
        rationale="Invalid FSM state = completely undefined behaviour — worst-case bug.",
    ),
    FormalPropertyRule(
        id="FP-FSM-003", library_source="Formal best practice",
        property_class="state_machine",
        title="FSM returns to IDLE from every state",
        description="Verify that from every state, there exists an input sequence that returns the FSM to IDLE within a bounded number of cycles.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{state} != {IDLE} |-> ##[1:{MAX_CYCLES}] {state} == {IDLE});"
        ),
        trigger_patterns=["state", "fsm", "idle"],
        severity="P1", property_type="assert",
        rationale="FSM that cannot return to IDLE represents a potential deadlock or hang.",
    ),
    FormalPropertyRule(
        id="FP-FSM-004", library_source="Formal best practice",
        property_class="state_machine",
        title="FSM output is defined for every state",
        description="Verify that all outputs driven by the FSM have defined values in every state — no implicit latches or X conditions.",
        sva_template="assert property (@(posedge {clk}) !$isunknown({output}));",
        trigger_patterns=["state", "fsm", "output"],
        severity="P0", property_type="assert",
        rationale="Undefined output in an FSM state causes downstream X propagation.",
    ),
    FormalPropertyRule(
        id="FP-FSM-005", library_source="Formal best practice",
        property_class="state_machine",
        title="FSM transitions are mutually exclusive",
        description="In each state, at most one transition condition should be true at any time. Verify no two outgoing transitions fire simultaneously.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{state} == {S} |-> $onehot0({transition_conditions}));"
        ),
        trigger_patterns=["state", "fsm", "transition", "next_state"],
        severity="P1", property_type="assert",
        rationale="Multiple true transitions = non-deterministic FSM = synthesis picks one arbitrarily.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Handshake Properties
# ═══════════════════════════════════════════════════════════════════════

_HANDSHAKE_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-HS-001", library_source="OVL pattern: ovl_handshake",
        property_class="protocol",
        title="Request eventually acknowledged",
        description="Every asserted request must eventually receive an acknowledgement within a bounded number of cycles.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{req} |-> ##[1:{MAX_LATENCY}] {ack});"
        ),
        trigger_patterns=["req", "ack", "request", "acknowledge"],
        severity="P0", property_type="assert",
        rationale="Unanswered request = system hang.",
    ),
    FormalPropertyRule(
        id="FP-HS-002", library_source="OVL pattern: ovl_handshake",
        property_class="protocol",
        title="No acknowledge without request",
        description="Acknowledgement must not assert when no request is pending.",
        sva_template="assert property (@(posedge {clk}) {ack} |-> {req} || $past({req}));",
        trigger_patterns=["req", "ack"],
        severity="P0", property_type="assert",
        rationale="Spurious ack confuses the requester — may advance state machine incorrectly.",
    ),
    FormalPropertyRule(
        id="FP-HS-003", library_source="Formal best practice",
        property_class="protocol",
        title="Valid does not drop before ready",
        description="Once valid is asserted, it must not deassert until ready is also asserted (handshake completes).",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{valid} && !{ready} |=> {valid});"
        ),
        trigger_patterns=["valid", "ready"],
        severity="P0", property_type="assert",
        rationale="Dropping valid before handshake = protocol violation on every valid-ready interface.",
    ),
    FormalPropertyRule(
        id="FP-HS-004", library_source="Formal best practice",
        property_class="protocol",
        title="Data stable during valid-and-not-ready",
        description="While valid is asserted and ready is not, all payload signals must remain stable.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{valid} && !{ready} |=> $stable({data}));"
        ),
        trigger_patterns=["valid", "ready", "data"],
        severity="P0", property_type="assert",
        rationale="Changing data during pending handshake = receiver samples wrong value.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# One-hot / Encoding Properties
# ═══════════════════════════════════════════════════════════════════════

_TLUL_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-TLUL-001",
        library_source="OpenTitan TileLink-UL ABV pattern",
        property_class="protocol",
        title="TL-UL A-channel valid stable until ready",
        description="Once a_valid is asserted, it must remain asserted until the A-channel handshake completes with a_ready.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{a_valid} && !{a_ready} |=> {a_valid});"
        ),
        trigger_patterns=["tlul", "tilelink", "a_valid", "a_ready"],
        severity="P0",
        property_type="assert",
        rationale="Dropping a_valid before a_ready loses a TileLink-UL request.",
    ),
    FormalPropertyRule(
        id="FP-TLUL-002",
        library_source="OpenTitan TileLink-UL ABV pattern",
        property_class="protocol",
        title="TL-UL A-channel payload stable while stalled",
        description="A-channel opcode, address, source, mask, size, and data fields must remain stable while a_valid is high and a_ready is low.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{a_valid} && !{a_ready} |=> $stable({a_payload}));"
        ),
        trigger_patterns=["a_opcode", "a_address", "a_source", "a_mask", "a_data"],
        severity="P0",
        property_type="assert",
        rationale="Changing the A-channel payload while stalled makes the receiver sample the wrong request.",
    ),
    FormalPropertyRule(
        id="FP-TLUL-003",
        library_source="OpenTitan TileLink-UL ABV pattern",
        property_class="protocol",
        title="TL-UL D-channel valid stable until ready",
        description="Once d_valid is asserted, it must remain asserted until the D-channel handshake completes with d_ready.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{d_valid} && !{d_ready} |=> {d_valid});"
        ),
        trigger_patterns=["tlul", "tilelink", "d_valid", "d_ready"],
        severity="P0",
        property_type="assert",
        rationale="Dropping d_valid before d_ready loses a TileLink-UL response.",
    ),
    FormalPropertyRule(
        id="FP-TLUL-004",
        library_source="OpenTitan TileLink-UL ABV pattern",
        property_class="protocol",
        title="TL-UL D-channel payload stable while stalled",
        description="D-channel opcode, source, sink, data, and error fields must remain stable while d_valid is high and d_ready is low.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{d_valid} && !{d_ready} |=> $stable({d_payload}));"
        ),
        trigger_patterns=["d_opcode", "d_source", "d_sink", "d_data", "d_error"],
        severity="P0",
        property_type="assert",
        rationale="Changing the D-channel payload while stalled makes the host sample the wrong response.",
    ),
    FormalPropertyRule(
        id="FP-TLUL-005",
        library_source="OpenTitan TileLink-UL ABV pattern",
        property_class="protocol",
        title="TL-UL request response reachability",
        description="Cover that an accepted A-channel request can lead to an accepted D-channel response within a bounded window.",
        sva_template=(
            "cover property (@(posedge {clk}) "
            "{a_valid} && {a_ready} ##[1:{MAX_LATENCY}] {d_valid} && {d_ready});"
        ),
        trigger_patterns=["a_valid", "a_ready", "d_valid", "d_ready"],
        severity="P1",
        property_type="cover",
        rationale="A request-response cover proves the environment can exercise the complete TL-UL transaction path.",
    ),
]


_ENCODING_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-ENC-001", library_source="OVL pattern: ovl_one_hot",
        property_class="encoding",
        title="Signal is one-hot encoded",
        description="Verify the signal has exactly one bit set at all times (or zero bits if one-hot-zero is allowed).",
        sva_template="assert property (@(posedge {clk}) $onehot({signal}));",
        trigger_patterns=["one_hot", "onehot", "select", "grant"],
        severity="P0", property_type="assert",
        rationale="Non-one-hot grant/select = multiple resources activated simultaneously.",
    ),
    FormalPropertyRule(
        id="FP-ENC-002", library_source="OVL pattern: ovl_zero_one_hot",
        property_class="encoding",
        title="Signal is zero-or-one-hot",
        description="Verify the signal has at most one bit set (zero bits allowed when no selection is active).",
        sva_template="assert property (@(posedge {clk}) $onehot0({signal}));",
        trigger_patterns=["select", "grant", "enable"],
        severity="P0", property_type="assert",
        rationale="Multiple bits set in a one-hot select = bus contention or multi-activation.",
    ),
    FormalPropertyRule(
        id="FP-ENC-003", library_source="Formal best practice",
        property_class="encoding",
        title="Gray code single-bit transition",
        description="Verify that consecutive values of a Gray-coded counter differ by exactly one bit.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "$onehot({gray_val} ^ $past({gray_val})));"
        ),
        trigger_patterns=["gray", "gray_code", "bin2gray"],
        severity="P0", property_type="assert",
        rationale="Multi-bit transition in Gray code = metastability hazard in CDC applications.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Arbiter Properties
# ═══════════════════════════════════════════════════════════════════════

_ARBITER_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-ARB-001", library_source="OVL pattern: ovl_arbiter",
        property_class="arbitration",
        title="At most one grant active at a time",
        description="In a mutual-exclusion arbiter, verify at most one grant is asserted at any time.",
        sva_template="assert property (@(posedge {clk}) $onehot0({grant}));",
        trigger_patterns=["grant", "gnt", "arbiter"],
        severity="P0", property_type="assert",
        rationale="Multiple simultaneous grants = shared resource corruption.",
    ),
    FormalPropertyRule(
        id="FP-ARB-002", library_source="OVL pattern: ovl_arbiter",
        property_class="arbitration",
        title="Grant implies request",
        description="A grant must only be asserted if the corresponding request is active.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "|({grant}) |-> |({grant} & {request}));"
        ),
        trigger_patterns=["grant", "request", "arbiter"],
        severity="P0", property_type="assert",
        rationale="Grant without request = unrequested bus access = data corruption.",
    ),
    FormalPropertyRule(
        id="FP-ARB-003", library_source="Formal best practice",
        property_class="arbitration",
        title="No starvation (liveness)",
        description="Every persistent request must eventually receive a grant within a bounded number of cycles.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{request}[{i}] |-> ##[1:{MAX_WAIT}] {grant}[{i}]);"
        ),
        trigger_patterns=["request", "grant", "fair", "round_robin"],
        severity="P0", property_type="assert",
        rationale="Starvation = one requester permanently blocked = system-level hang.",
    ),
    FormalPropertyRule(
        id="FP-ARB-004", library_source="Formal best practice",
        property_class="arbitration",
        title="Round-robin fairness",
        description="In a round-robin arbiter, verify that after granting requester N, the next grant under contention goes to requester (N+1) mod NUM_REQ.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{grant}[{N}] && &{request} ##1 !{grant}[{N}] |-> {grant}[({N}+1) % {NUM}]);"
        ),
        trigger_patterns=["round_robin", "rr", "arbiter", "fair"],
        severity="P1", property_type="assert",
        rationale="Unfair round-robin degrades into fixed priority — starves some requestors.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Reset Properties
# ═══════════════════════════════════════════════════════════════════════

_RESET_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-RST-001", library_source="Formal best practice",
        property_class="reset",
        title="All outputs at reset value during reset",
        description="While reset is active, verify all outputs are at their documented reset values.",
        sva_template="assert property (@(posedge {clk}) !{rstn} |-> {output} == {RESET_VALUE});",
        trigger_patterns=["rst", "reset", "rstn"],
        severity="P0", property_type="assert",
        rationale="Output not at reset value during reset = downstream sees wrong initial state.",
    ),
    FormalPropertyRule(
        id="FP-RST-002", library_source="Formal best practice",
        property_class="reset",
        title="State machine in IDLE after reset",
        description="After reset deassertion, verify the FSM is in its initial (IDLE) state.",
        sva_template="assert property (@(posedge {clk}) $rose({rstn}) |-> ##1 {state} == {IDLE});",
        trigger_patterns=["rst", "reset", "state", "fsm"],
        severity="P0", property_type="assert",
        rationale="FSM not in IDLE after reset = first transaction is processed incorrectly.",
    ),
    FormalPropertyRule(
        id="FP-RST-003", library_source="Formal best practice",
        property_class="reset",
        title="No output glitch during reset assertion",
        description="Verify outputs transition cleanly to reset values without glitching to intermediate values.",
        sva_template="assert property (@(posedge {clk}) $fell({rstn}) |=> {output} == {RESET_VALUE});",
        trigger_patterns=["rst", "reset", "glitch", "output"],
        severity="P1", property_type="assert",
        rationale="Glitch during reset can be latched by downstream logic not in reset.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# No-X Properties
# ═══════════════════════════════════════════════════════════════════════

_NO_X_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-NOX-001", library_source="Formal best practice",
        property_class="data_integrity",
        title="No X on outputs after reset",
        description="After reset deassertion, verify no output signal contains X (unknown) values.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{rstn} |-> !$isunknown({output}));"
        ),
        trigger_patterns=["output", "data_out", "valid"],
        severity="P0", property_type="assert",
        rationale="X on output = uninitialized flip-flop or missing reset — unpredictable downstream behaviour.",
    ),
    FormalPropertyRule(
        id="FP-NOX-002", library_source="Formal best practice",
        property_class="data_integrity",
        title="No X on control signals ever",
        description="Control signals (enable, select, write_enable) must never be X at any time.",
        sva_template="assert property (@(posedge {clk}) !$isunknown({control}));",
        trigger_patterns=["enable", "select", "wr_en", "rd_en"],
        severity="P0", property_type="assert",
        rationale="X on control signal = non-deterministic behaviour that differs between simulation and silicon.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Timing Properties
# ═══════════════════════════════════════════════════════════════════════

_TIMING_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-TIM-001", library_source="OVL pattern: ovl_time",
        property_class="timing",
        title="Signal asserts within maximum latency",
        description="After a trigger event, verify the response signal asserts within the specified maximum latency.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{trigger} |-> ##[1:{MAX_LATENCY}] {response});"
        ),
        trigger_patterns=["latency", "delay", "response"],
        severity="P1", property_type="assert",
        rationale="Response beyond max latency may cause upstream timeout or system-level deadline miss.",
    ),
    FormalPropertyRule(
        id="FP-TIM-002", library_source="OVL pattern: ovl_width",
        property_class="timing",
        title="Pulse width minimum check",
        description="Verify a signal stays asserted for at least the minimum required width.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "$rose({signal}) |-> {signal}[*{MIN_WIDTH}]);"
        ),
        trigger_patterns=["pulse", "width", "strobe"],
        severity="P1", property_type="assert",
        rationale="Too-short pulse may not be captured by downstream synchronous logic.",
    ),
    FormalPropertyRule(
        id="FP-TIM-003", library_source="OVL pattern: ovl_width",
        property_class="timing",
        title="Pulse width maximum check",
        description="Verify a signal does not stay asserted longer than the maximum allowed width.",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "$rose({signal}) |-> ##[1:{MAX_WIDTH}] $fell({signal}));"
        ),
        trigger_patterns=["pulse", "width", "duration"],
        severity="P1", property_type="assert",
        rationale="Too-long assertion may violate protocol timing or starve other operations.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Aggregate + Lookup
# ═══════════════════════════════════════════════════════════════════════

_CSR_INTERRUPT_PROPERTIES: List[FormalPropertyRule] = [
    FormalPropertyRule(
        id="FP-CSR-001", library_source="Formal best practice",
        property_class="register",
        title="Read-only field ignores writes",
        description="A software write to a read-only field must not change the field value.",
        sva_template=(
            "assert property (@(posedge {clk}) disable iff (!{rstn}) "
            "{wr_en} && {addr_hit} |=> {ro_field} == $past({ro_field}));"
        ),
        trigger_patterns=["ro", "read_only", "status", "wr_en", "csr"],
        severity="P1", property_type="assert",
        rationale="Writable read-only status lets firmware forge hardware state.",
    ),
    FormalPropertyRule(
        id="FP-CSR-002", library_source="Formal best practice",
        property_class="register",
        title="Byte-enable preserves disabled bytes",
        description="When byte enables are used, bytes with enable=0 must preserve their previous value.",
        sva_template=(
            "assert property (@(posedge {clk}) disable iff (!{rstn}) "
            "{wr_en} && !{byte_en}[{i}] |=> {reg_byte}[{i}] == $past({reg_byte}[{i}]));"
        ),
        trigger_patterns=["byte_en", "strobe", "strb", "wstrb", "wmask"],
        severity="P1", property_type="assert",
        rationale="Partial writes that modify disabled lanes corrupt adjacent fields.",
    ),
    FormalPropertyRule(
        id="FP-CSR-003", library_source="Formal best practice",
        property_class="register",
        title="Write-one-clear event is not lost",
        description="If software clears a W1C bit in the same cycle hardware sets it, the new event must remain visible according to precedence policy.",
        sva_template=(
            "assert property (@(posedge {clk}) disable iff (!{rstn}) "
            "{sw_clear} && {hw_set} |=> {status_bit});"
        ),
        trigger_patterns=["w1c", "clear", "clr", "status", "pending", "event"],
        severity="P0", property_type="assert",
        rationale="Clear/set races are a common source of lost interrupts and missed status.",
    ),
    FormalPropertyRule(
        id="FP-IRQ-001", library_source="Formal best practice",
        property_class="interrupt",
        title="Interrupt sticky until clear",
        description="Once an interrupt/status event occurs, pending must remain asserted until software clears it.",
        sva_template=(
            "assert property (@(posedge {clk}) disable iff (!{rstn}) "
            "{event} |=> {pending} throughout (!{clear}));"
        ),
        trigger_patterns=["irq", "interrupt", "pending", "clear", "event"],
        severity="P0", property_type="assert",
        rationale="Non-sticky pending bits can disappear before firmware polls them.",
    ),
    FormalPropertyRule(
        id="FP-IRQ-002", library_source="Formal best practice",
        property_class="interrupt",
        title="Masked interrupt still records pending",
        description="When an event occurs while interrupt output is masked, pending/status must still be set for later service.",
        sva_template=(
            "assert property (@(posedge {clk}) disable iff (!{rstn}) "
            "{event} && !{irq_enable} |=> {pending} && !{irq_out});"
        ),
        trigger_patterns=["irq", "interrupt", "mask", "enable", "pending"],
        severity="P1", property_type="assert",
        rationale="Masked event that does not set pending is lost when the mask is removed.",
    ),
    FormalPropertyRule(
        id="FP-PWR-001", library_source="Formal best practice",
        property_class="power_clock",
        title="Clock gate produces no edge while disabled",
        description="When clock gate enable is low, gated clock must not produce active edges.",
        sva_template=(
            "assert property (@(posedge {src_clk}) !{clk_en} |=> !$rose({gated_clk}));"
        ),
        trigger_patterns=["clk_en", "cg_en", "gated_clk", "clock_gate"],
        severity="P0", property_type="assert",
        rationale="A spurious gated-clock edge can corrupt retained or idle state.",
    ),
]

FORMAL_PROPERTY_RULES: List[FormalPropertyRule] = (
    _FIFO_PROPERTIES
    + _COUNTER_PROPERTIES
    + _FSM_PROPERTIES
    + _HANDSHAKE_PROPERTIES
    + _TLUL_PROPERTIES
    + _ENCODING_PROPERTIES
    + _ARBITER_PROPERTIES
    + _RESET_PROPERTIES
    + _NO_X_PROPERTIES
    + _TIMING_PROPERTIES
    + _CSR_INTERRUPT_PROPERTIES
)


def get_all_formal_properties() -> List[FormalPropertyRule]:
    """Return all formal property rules."""
    return list(FORMAL_PROPERTY_RULES)


def get_formal_properties_by_class(prop_class: str) -> List[FormalPropertyRule]:
    """Return formal properties of a specific class."""
    cls = prop_class.lower()
    return [p for p in FORMAL_PROPERTY_RULES if cls in p.property_class.lower()]


def match_formal_properties(signal_names: List[str]) -> List[FormalPropertyRule]:
    """Return formal properties whose trigger patterns match any given signal name."""
    lower_signals = {s.lower() for s in signal_names}
    results = []
    for p in FORMAL_PROPERTY_RULES:
        for pattern in p.trigger_patterns:
            if any(pattern.lower() in sig for sig in lower_signals):
                results.append(p)
                break
    return results
