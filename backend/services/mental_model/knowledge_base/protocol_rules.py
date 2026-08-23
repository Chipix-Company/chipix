"""
Protocol Compliance Rules — Knowledge Base

Every rule in this file traces to an official protocol specification or
well-documented de-facto standard. The `spec_ref` field identifies the
exact source document and section.

Sources:
  - ARM AMBA AXI4 Protocol Specification (IHI 0022)
  - ARM AMBA APB Protocol Specification (IHI 0024)
  - ARM AMBA AHB-Lite Protocol Specification (IHI 0033)
  - NXP I2C Specification (UM10204)
  - IEEE 1149.1 (JTAG)
  - Motorola SPI de-facto standard
  - 16550 UART de-facto standard
  - OpenCores Wishbone B4 Specification
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ProtocolRule:
    """A single protocol compliance rule derived from an official specification.

    Attributes:
        id:              Unique rule identifier (e.g. ``"AXI-HAND-01"``).
        protocol:        Protocol family this rule belongs to.
        text:            Human-readable rule statement.
        category:        Rule category for grouping.
        severity:        ``"P0"`` = must check, ``"P1"`` = should check,
                         ``"P2"`` = nice to check.
        spec_ref:        Source document and section reference.
        check_type:      How this rule should be verified.
        sva_template:    Optional SVA property template with ``{signal}``
                         placeholders.
        applicable_roles: Which interface roles this rule applies to.
        negative_test:   How to test *violation* of this rule.
    """

    id: str
    protocol: str
    text: str
    category: str
    severity: str
    spec_ref: str
    check_type: str
    sva_template: str = ""
    applicable_roles: List[str] = field(default_factory=lambda: ["both"])
    negative_test: str = ""


# ═══════════════════════════════════════════════════════════════════════
# AXI4 Rules  (ARM IHI 0022)
# ═══════════════════════════════════════════════════════════════════════

_AXI4_RULES: List[ProtocolRule] = [
    # --- Handshake rules (Section A3.2.1) ---
    ProtocolRule(
        id="AXI-HAND-01",
        protocol="AXI4",
        text="VALID must not depend on READY — source must assert VALID independently",
        category="deadlock",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.2.1",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "$rose({valid}) |-> !$past({ready}));"
        ),
        negative_test="Hold VALID low until READY asserts — should trigger deadlock",
    ),
    ProtocolRule(
        id="AXI-HAND-02",
        protocol="AXI4",
        text="Once VALID is asserted it must remain asserted until the handshake occurs (VALID && READY)",
        category="handshake",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.2.1",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{valid} && !{ready} |=> {valid});"
        ),
        negative_test="Assert VALID then deassert before READY — protocol violation",
    ),
    ProtocolRule(
        id="AXI-HAND-03",
        protocol="AXI4",
        text="Source must not change payload signals while VALID is asserted and READY is low",
        category="stability",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.2.1",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{valid} && !{ready} |=> $stable({payload}));"
        ),
        negative_test="Change address/data while VALID high and READY low",
    ),
    ProtocolRule(
        id="AXI-HAND-04",
        protocol="AXI4",
        text="READY may depend on VALID (allowed dependency direction)",
        category="handshake",
        severity="P1",
        spec_ref="ARM IHI 0022, Section A3.2.1",
        check_type="cover",
        sva_template=(
            "cover property (@(posedge {clk}) "
            "$rose({valid}) ##[0:$] $rose({ready}));"
        ),
    ),
    ProtocolRule(
        id="AXI-HAND-05",
        protocol="AXI4",
        text="Transfer occurs on rising clock edge when both VALID and READY are HIGH",
        category="handshake",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.2.1",
        check_type="cover",
        sva_template=(
            "cover property (@(posedge {clk}) {valid} && {ready});"
        ),
    ),

    # --- Write channel rules (Section A3.3, A3.4, A5.3) ---
    ProtocolRule(
        id="AXI-WR-01",
        protocol="AXI4",
        text="Write data order on W channel must match AW address order",
        category="ordering",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A5.3",
        check_type="sequence_check",
        negative_test="Send W data for second transaction before first AW completes",
    ),
    ProtocolRule(
        id="AXI-WR-02",
        protocol="AXI4",
        text="WLAST must be asserted on the final write data beat of a burst",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.4.1",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{wvalid} && {wready} && ({beat_count} == {awlen}) |-> {wlast});"
        ),
        negative_test="Complete burst without asserting WLAST",
    ),
    ProtocolRule(
        id="AXI-WR-03",
        protocol="AXI4",
        text="Write response BVALID must not assert before all write data received (after WLAST)",
        category="ordering",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.3",
        check_type="assertion",
        applicable_roles=["slave"],
        negative_test="Assert BVALID before WLAST is received",
    ),
    ProtocolRule(
        id="AXI-WR-04",
        protocol="AXI4",
        text="BRESP must be OKAY(00), EXOKAY(01), SLVERR(10), or DECERR(11) only",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.4.4",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{bvalid} |-> {bresp} inside {2'b00, 2'b01, 2'b10, 2'b11});"
        ),
    ),
    ProtocolRule(
        id="AXI-WR-05",
        protocol="AXI4",
        text="Write data interleaving is not permitted in AXI4 (deprecated from AXI3)",
        category="ordering",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A5.3",
        check_type="assertion",
        negative_test="Interleave write data from different transactions",
    ),

    # --- Read channel rules (Section A3.4.3, A3.4.4) ---
    ProtocolRule(
        id="AXI-RD-01",
        protocol="AXI4",
        text="RLAST must be asserted on the final read data beat of a burst",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.4.3",
        check_type="assertion",
        applicable_roles=["slave"],
    ),
    ProtocolRule(
        id="AXI-RD-02",
        protocol="AXI4",
        text="Read data must be valid when RVALID is asserted",
        category="stability",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.4.3",
        check_type="assertion",
    ),
    ProtocolRule(
        id="AXI-RD-03",
        protocol="AXI4",
        text="RRESP must correctly indicate error status for each beat",
        category="error",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.4.4",
        check_type="assertion",
    ),

    # --- Ordering rules (Section A6.3, A6.4) ---
    ProtocolRule(
        id="AXI-ORD-01",
        protocol="AXI4",
        text="Transactions with the same ID must complete in issue order",
        category="ordering",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A6.3",
        check_type="sequence_check",
        negative_test="Return responses for same-ID transactions out of order",
    ),
    ProtocolRule(
        id="AXI-ORD-02",
        protocol="AXI4",
        text="Transactions with different IDs may complete in any order",
        category="ordering",
        severity="P1",
        spec_ref="ARM IHI 0022, Section A6.3",
        check_type="cover",
    ),
    ProtocolRule(
        id="AXI-ORD-03",
        protocol="AXI4",
        text="No ordering constraint between read and write channels",
        category="ordering",
        severity="P1",
        spec_ref="ARM IHI 0022, Section A6.4",
        check_type="cover",
    ),

    # --- Burst rules (Section A3.4.1) ---
    ProtocolRule(
        id="AXI-BURST-01",
        protocol="AXI4",
        text="A burst must not cross a 4KB address boundary",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.4.1",
        check_type="assertion",
        negative_test="Issue burst that would cross 4KB boundary",
    ),
    ProtocolRule(
        id="AXI-BURST-02",
        protocol="AXI4",
        text="INCR burst length must be 1 to 256 beats",
        category="protocol",
        severity="P1",
        spec_ref="ARM IHI 0022, Section A3.4.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="AXI-BURST-03",
        protocol="AXI4",
        text="WRAP burst length must be 2, 4, 8, or 16",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A3.4.1",
        check_type="assertion",
    ),

    # --- Reset rules (Section A10.1) ---
    ProtocolRule(
        id="AXI-RESET-01",
        protocol="AXI4",
        text="During reset, master must drive all VALID signals LOW",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0022, Section A10.1",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "!{rstn} |-> !{awvalid} && !{wvalid} && !{arvalid});"
        ),
        applicable_roles=["master"],
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# AXI4-Lite Rules  (ARM IHI 0022, Appendix B)
# ═══════════════════════════════════════════════════════════════════════

_AXI4_LITE_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="AXIL-01",
        protocol="AXI4-Lite",
        text="All transfers are single-beat (burst length is always 1)",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0022, Section B1.1",
        check_type="assertion",
        negative_test="Attempt multi-beat burst on AXI4-Lite interface",
    ),
    ProtocolRule(
        id="AXIL-02",
        protocol="AXI4-Lite",
        text="All accesses must be the same width as the data bus",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0022, Section B1.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="AXIL-03",
        protocol="AXI4-Lite",
        text="WSTRB must be consistent with address alignment and transfer size",
        category="protocol",
        severity="P1",
        spec_ref="ARM IHI 0022, Section B1.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="AXIL-04",
        protocol="AXI4-Lite",
        text="Exclusive accesses are not supported in AXI4-Lite",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0022, Section B1.1",
        check_type="assertion",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# AXI4-Stream Rules  (ARM IHI 0051)
# ═══════════════════════════════════════════════════════════════════════

_AXI4_STREAM_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="AXIS-01",
        protocol="AXI4-Stream",
        text="TVALID must not depend on TREADY (deadlock prevention)",
        category="deadlock",
        severity="P0",
        spec_ref="ARM IHI 0051, Section 2.2",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "$rose({tvalid}) |-> !$past({tready}));"
        ),
    ),
    ProtocolRule(
        id="AXIS-02",
        protocol="AXI4-Stream",
        text="Once TVALID is asserted, TDATA and control signals must not change until handshake",
        category="stability",
        severity="P0",
        spec_ref="ARM IHI 0051, Section 2.2",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{tvalid} && !{tready} |=> $stable({tdata}));"
        ),
    ),
    ProtocolRule(
        id="AXIS-03",
        protocol="AXI4-Stream",
        text="TLAST marks the boundary of a packet or frame",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0051, Section 2.3",
        check_type="cover",
    ),
    ProtocolRule(
        id="AXIS-04",
        protocol="AXI4-Stream",
        text="NULL bytes are indicated by TKEEP and TSTRB signals",
        category="protocol",
        severity="P1",
        spec_ref="ARM IHI 0051, Section 2.4",
        check_type="assertion",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# APB Rules  (ARM IHI 0024)
# ═══════════════════════════════════════════════════════════════════════

_APB_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="APB-SETUP-01",
        protocol="APB",
        text="PSEL must be asserted during the SETUP phase (one cycle before PENABLE)",
        category="timing",
        severity="P0",
        spec_ref="ARM IHI 0024, Section 4.1",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "$rose({penable}) |-> $past({psel}));"
        ),
    ),
    ProtocolRule(
        id="APB-SETUP-02",
        protocol="APB",
        text="PADDR, PWRITE, and PWDATA must be valid during the SETUP phase",
        category="stability",
        severity="P0",
        spec_ref="ARM IHI 0024, Section 4.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="APB-ACCESS-01",
        protocol="APB",
        text="PENABLE must be asserted at the start of the ACCESS phase",
        category="timing",
        severity="P0",
        spec_ref="ARM IHI 0024, Section 4.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="APB-ACCESS-02",
        protocol="APB",
        text="PADDR, PWRITE, PSEL, PENABLE, and PWDATA must remain stable during the ACCESS phase",
        category="stability",
        severity="P0",
        spec_ref="ARM IHI 0024, Section 4.2",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{psel} && {penable} && !{pready} |=> "
            "$stable({paddr}) && $stable({pwrite}) && $stable({pwdata}));"
        ),
        negative_test="Change PADDR during ACCESS phase while PREADY is low",
    ),
    ProtocolRule(
        id="APB-READY-01",
        protocol="APB",
        text="PREADY may be held LOW to insert wait states in the ACCESS phase",
        category="timing",
        severity="P1",
        spec_ref="ARM IHI 0024, Section 4.2",
        check_type="cover",
    ),
    ProtocolRule(
        id="APB-READY-02",
        protocol="APB",
        text="Transfer completes on rising clock edge when PENABLE and PREADY are both HIGH",
        category="handshake",
        severity="P0",
        spec_ref="ARM IHI 0024, Section 4.2",
        check_type="assertion",
    ),
    ProtocolRule(
        id="APB-ERR-01",
        protocol="APB",
        text="PSLVERR is valid only during the last cycle of a transfer (PSEL && PENABLE && PREADY)",
        category="error",
        severity="P0",
        spec_ref="ARM IHI 0024, Section 4.3",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{pslverr} |-> {psel} && {penable} && {pready});"
        ),
        negative_test="Assert PSLVERR when PENABLE or PREADY is low",
    ),
    ProtocolRule(
        id="APB-TIMING-01",
        protocol="APB",
        text="Every APB transfer takes at least two clock cycles (one SETUP, one ACCESS)",
        category="timing",
        severity="P0",
        spec_ref="ARM IHI 0024, Section 4.1",
        check_type="assertion",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# AHB-Lite Rules  (ARM IHI 0033)
# ═══════════════════════════════════════════════════════════════════════

_AHB_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="AHB-TRANS-01",
        protocol="AHB-Lite",
        text="HTRANS NONSEQ (2'b10) indicates the first transfer of a burst",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0033, Section 3.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="AHB-TRANS-02",
        protocol="AHB-Lite",
        text="HTRANS SEQ (2'b11) indicates a continuation beat of a burst",
        category="protocol",
        severity="P1",
        spec_ref="ARM IHI 0033, Section 3.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="AHB-TRANS-03",
        protocol="AHB-Lite",
        text="HTRANS BUSY (2'b01) may be used for master delay within a burst",
        category="protocol",
        severity="P1",
        spec_ref="ARM IHI 0033, Section 3.1",
        check_type="cover",
    ),
    ProtocolRule(
        id="AHB-READY-01",
        protocol="AHB-Lite",
        text="HREADY LOW inserts wait states — address and data phases are extended",
        category="timing",
        severity="P0",
        spec_ref="ARM IHI 0033, Section 3.2",
        check_type="cover",
    ),
    ProtocolRule(
        id="AHB-ERR-01",
        protocol="AHB-Lite",
        text="Error response requires exactly two cycles",
        category="error",
        severity="P0",
        spec_ref="ARM IHI 0033, Section 3.4",
        check_type="assertion",
    ),
    ProtocolRule(
        id="AHB-ERR-02",
        protocol="AHB-Lite",
        text="First error cycle: HREADY LOW and HRESP HIGH",
        category="error",
        severity="P0",
        spec_ref="ARM IHI 0033, Section 3.4",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "$rose({hresp}) |-> !{hready});"
        ),
        applicable_roles=["slave"],
    ),
    ProtocolRule(
        id="AHB-ERR-03",
        protocol="AHB-Lite",
        text="Second error cycle: HREADY HIGH and HRESP HIGH",
        category="error",
        severity="P0",
        spec_ref="ARM IHI 0033, Section 3.4",
        check_type="assertion",
        applicable_roles=["slave"],
    ),
    ProtocolRule(
        id="AHB-ERR-04",
        protocol="AHB-Lite",
        text="Master must set HTRANS to IDLE after receiving an ERROR during burst",
        category="error",
        severity="P0",
        spec_ref="ARM IHI 0033, Section 3.4",
        check_type="assertion",
        applicable_roles=["master"],
    ),
    ProtocolRule(
        id="AHB-BURST-01",
        protocol="AHB-Lite",
        text="Burst must not cross a 1KB address boundary",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0033, Section 3.5",
        check_type="assertion",
    ),
    ProtocolRule(
        id="AHB-RESET-01",
        protocol="AHB-Lite",
        text="After reset, master must drive HTRANS to IDLE",
        category="protocol",
        severity="P0",
        spec_ref="ARM IHI 0033, Section 5.1",
        check_type="assertion",
        applicable_roles=["master"],
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Valid-Ready Handshake  (Industry standard)
# ═══════════════════════════════════════════════════════════════════════

_TLUL_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="TLUL-A-01",
        protocol="TileLink-UL",
        text="A-channel valid must remain asserted until a_valid and a_ready complete the request handshake",
        category="handshake",
        severity="P0",
        spec_ref="OpenTitan TileLink-UL device interface specification",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{a_valid} && !{a_ready} |=> {a_valid});"
        ),
        applicable_roles=["host", "device", "both"],
        negative_test="Drop a_valid before a_ready is observed.",
    ),
    ProtocolRule(
        id="TLUL-A-02",
        protocol="TileLink-UL",
        text="A-channel request payload must remain stable while a_valid is high and a_ready is low",
        category="stability",
        severity="P0",
        spec_ref="OpenTitan TileLink-UL device interface specification",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{a_valid} && !{a_ready} |=> $stable({a_payload}));"
        ),
        applicable_roles=["host", "device", "both"],
        negative_test="Change a_opcode, a_address, a_size, a_source, a_mask, or a_data while the request is stalled.",
    ),
    ProtocolRule(
        id="TLUL-D-01",
        protocol="TileLink-UL",
        text="D-channel valid must remain asserted until d_valid and d_ready complete the response handshake",
        category="handshake",
        severity="P0",
        spec_ref="OpenTitan TileLink-UL device interface specification",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{d_valid} && !{d_ready} |=> {d_valid});"
        ),
        applicable_roles=["host", "device", "both"],
        negative_test="Drop d_valid before d_ready is observed.",
    ),
    ProtocolRule(
        id="TLUL-D-02",
        protocol="TileLink-UL",
        text="D-channel response payload must remain stable while d_valid is high and d_ready is low",
        category="stability",
        severity="P0",
        spec_ref="OpenTitan TileLink-UL device interface specification",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{d_valid} && !{d_ready} |=> $stable({d_payload}));"
        ),
        applicable_roles=["host", "device", "both"],
        negative_test="Change d_opcode, d_source, d_sink, d_data, or d_error while the response is stalled.",
    ),
    ProtocolRule(
        id="TLUL-RSP-01",
        protocol="TileLink-UL",
        text="Accepted A-channel requests should be covered with corresponding D-channel responses",
        category="request_response",
        severity="P1",
        spec_ref="OpenTitan TileLink-UL device interface specification",
        check_type="cover",
        sva_template=(
            "cover property (@(posedge {clk}) "
            "{a_valid} && {a_ready} ##[1:{max_latency}] {d_valid} && {d_ready});"
        ),
        applicable_roles=["host", "device", "both"],
    ),
]


_VALID_READY_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="VR-01",
        protocol="valid-ready",
        text="Data must be stable while valid is HIGH and ready is LOW",
        category="stability",
        severity="P0",
        spec_ref="Industry standard handshake protocol",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{valid} && !{ready} |=> $stable({data}));"
        ),
    ),
    ProtocolRule(
        id="VR-02",
        protocol="valid-ready",
        text="Valid must not depend combinationally on ready (deadlock prevention)",
        category="deadlock",
        severity="P0",
        spec_ref="Industry standard handshake protocol",
        check_type="assertion",
    ),
    ProtocolRule(
        id="VR-03",
        protocol="valid-ready",
        text="Once valid is asserted, it must remain asserted until handshake completes",
        category="handshake",
        severity="P0",
        spec_ref="Industry standard handshake protocol",
        check_type="assertion",
        sva_template=(
            "assert property (@(posedge {clk}) "
            "{valid} && !{ready} |=> {valid});"
        ),
    ),
    ProtocolRule(
        id="VR-04",
        protocol="valid-ready",
        text="Ready may deassert at any time unless specific protocol forbids it",
        category="handshake",
        severity="P1",
        spec_ref="Industry standard handshake protocol",
        check_type="cover",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# SPI Rules  (Motorola de-facto standard)
# ═══════════════════════════════════════════════════════════════════════

_SPI_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="SPI-01",
        protocol="SPI",
        text="Data is sampled on the active clock edge per CPOL/CPHA mode configuration",
        category="timing",
        severity="P0",
        spec_ref="Motorola SPI specification",
        check_type="assertion",
    ),
    ProtocolRule(
        id="SPI-02",
        protocol="SPI",
        text="Chip select (CS/SS) must frame the entire SPI transaction",
        category="protocol",
        severity="P0",
        spec_ref="Motorola SPI specification",
        check_type="assertion",
    ),
    ProtocolRule(
        id="SPI-03",
        protocol="SPI",
        text="Bit order (MSB-first or LSB-first) must match configuration",
        category="protocol",
        severity="P1",
        spec_ref="Motorola SPI specification",
        check_type="sequence_check",
    ),
    ProtocolRule(
        id="SPI-04",
        protocol="SPI",
        text="Clock idle state must match CPOL setting",
        category="timing",
        severity="P1",
        spec_ref="Motorola SPI specification",
        check_type="assertion",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# I2C Rules  (NXP UM10204)
# ═══════════════════════════════════════════════════════════════════════

_I2C_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="I2C-01",
        protocol="I2C",
        text="START condition: SDA transitions from HIGH to LOW while SCL is HIGH",
        category="protocol",
        severity="P0",
        spec_ref="NXP UM10204, Section 3.1.3",
        check_type="assertion",
    ),
    ProtocolRule(
        id="I2C-02",
        protocol="I2C",
        text="STOP condition: SDA transitions from LOW to HIGH while SCL is HIGH",
        category="protocol",
        severity="P0",
        spec_ref="NXP UM10204, Section 3.1.4",
        check_type="assertion",
    ),
    ProtocolRule(
        id="I2C-03",
        protocol="I2C",
        text="Data on SDA must be stable during the HIGH period of SCL",
        category="stability",
        severity="P0",
        spec_ref="NXP UM10204, Section 3.1.2",
        check_type="assertion",
    ),
    ProtocolRule(
        id="I2C-04",
        protocol="I2C",
        text="ACK/NACK must be sent after every byte transfer",
        category="protocol",
        severity="P0",
        spec_ref="NXP UM10204, Section 3.1.6",
        check_type="assertion",
    ),
    ProtocolRule(
        id="I2C-05",
        protocol="I2C",
        text="Clock stretching: slave may hold SCL LOW to slow down the master",
        category="timing",
        severity="P1",
        spec_ref="NXP UM10204, Section 3.1.9",
        check_type="cover",
    ),
    ProtocolRule(
        id="I2C-06",
        protocol="I2C",
        text="Repeated START condition is allowed without issuing a STOP first",
        category="protocol",
        severity="P1",
        spec_ref="NXP UM10204, Section 3.1.10",
        check_type="cover",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# JTAG Rules  (IEEE 1149.1)
# ═══════════════════════════════════════════════════════════════════════

_JTAG_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="JTAG-01",
        protocol="JTAG",
        text="TAP state machine must follow IEEE 1149.1 state transitions",
        category="protocol",
        severity="P0",
        spec_ref="IEEE 1149.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="JTAG-02",
        protocol="JTAG",
        text="TMS is sampled at the rising edge of TCK to drive state transitions",
        category="timing",
        severity="P0",
        spec_ref="IEEE 1149.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="JTAG-03",
        protocol="JTAG",
        text="TDI is sampled at rising TCK edge; TDO changes on falling TCK edge",
        category="timing",
        severity="P0",
        spec_ref="IEEE 1149.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="JTAG-04",
        protocol="JTAG",
        text="Five consecutive TMS=1 with TCK toggles must reset TAP to Test-Logic-Reset",
        category="protocol",
        severity="P0",
        spec_ref="IEEE 1149.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="JTAG-05",
        protocol="JTAG",
        text="Instruction register must load to implementation-specified reset value after TAP reset",
        category="protocol",
        severity="P1",
        spec_ref="IEEE 1149.1",
        check_type="assertion",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# UART Rules  (16550 / de-facto standard)
# ═══════════════════════════════════════════════════════════════════════

_UART_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="UART-01",
        protocol="UART",
        text="Start bit is logic LOW for one bit period",
        category="timing",
        severity="P0",
        spec_ref="16550 UART de-facto standard",
        check_type="assertion",
    ),
    ProtocolRule(
        id="UART-02",
        protocol="UART",
        text="Stop bit(s) must be logic HIGH for one or two bit periods",
        category="timing",
        severity="P0",
        spec_ref="16550 UART de-facto standard",
        check_type="assertion",
    ),
    ProtocolRule(
        id="UART-03",
        protocol="UART",
        text="Parity bit must match configured parity type (odd, even, or none)",
        category="protocol",
        severity="P0",
        spec_ref="16550 UART de-facto standard",
        check_type="assertion",
        negative_test="Send byte with incorrect parity — verify error flag",
    ),
    ProtocolRule(
        id="UART-04",
        protocol="UART",
        text="Frame error detected when stop bit is not HIGH",
        category="error",
        severity="P0",
        spec_ref="16550 UART de-facto standard",
        check_type="assertion",
        negative_test="Corrupt stop bit — verify frame error interrupt",
    ),
    ProtocolRule(
        id="UART-05",
        protocol="UART",
        text="Overrun error if new byte arrives before previous byte is read from RX buffer",
        category="error",
        severity="P1",
        spec_ref="16550 UART de-facto standard",
        check_type="sequence_check",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Wishbone Rules  (OpenCores Wishbone B4)
# ═══════════════════════════════════════════════════════════════════════

_WISHBONE_RULES: List[ProtocolRule] = [
    ProtocolRule(
        id="WB-01",
        protocol="Wishbone",
        text="CYC must be asserted for the entire duration of the bus cycle",
        category="protocol",
        severity="P0",
        spec_ref="OpenCores Wishbone B4, Section 3.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="WB-02",
        protocol="Wishbone",
        text="STB asserted by master to indicate a valid transfer cycle",
        category="protocol",
        severity="P0",
        spec_ref="OpenCores Wishbone B4, Section 3.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="WB-03",
        protocol="Wishbone",
        text="ACK asserted by slave to indicate successful transfer completion",
        category="handshake",
        severity="P0",
        spec_ref="OpenCores Wishbone B4, Section 3.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="WB-04",
        protocol="Wishbone",
        text="ERR asserted by slave to indicate an error condition",
        category="error",
        severity="P0",
        spec_ref="OpenCores Wishbone B4, Section 3.1",
        check_type="assertion",
    ),
    ProtocolRule(
        id="WB-05",
        protocol="Wishbone",
        text="RTY asserted by slave to request the master to retry the transfer",
        category="protocol",
        severity="P1",
        spec_ref="OpenCores Wishbone B4, Section 3.1",
        check_type="cover",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Aggregate list + lookup helpers
# ═══════════════════════════════════════════════════════════════════════

PROTOCOL_RULES: List[ProtocolRule] = (
    _AXI4_RULES
    + _AXI4_LITE_RULES
    + _AXI4_STREAM_RULES
    + _APB_RULES
    + _AHB_RULES
    + _TLUL_RULES
    + _VALID_READY_RULES
    + _SPI_RULES
    + _I2C_RULES
    + _JTAG_RULES
    + _UART_RULES
    + _WISHBONE_RULES
)

_PROTOCOL_INDEX: Dict[str, List[ProtocolRule]] = {}


def _ensure_index() -> Dict[str, List[ProtocolRule]]:
    """Build a case-insensitive protocol → rules index (lazy)."""
    if not _PROTOCOL_INDEX:
        for rule in PROTOCOL_RULES:
            key = rule.protocol.lower()
            _PROTOCOL_INDEX.setdefault(key, []).append(rule)
    return _PROTOCOL_INDEX


def get_rules_for_protocol(protocol_name: str) -> List[ProtocolRule]:
    """Return all rules for a given protocol (case-insensitive match).

    Also matches partial names: ``"axi"`` matches ``"AXI4"``,
    ``"AXI4-Lite"``, and ``"AXI4-Stream"``.
    """
    idx = _ensure_index()
    needle = protocol_name.lower().replace(" ", "").replace("_", "").replace("-", "")
    results: List[ProtocolRule] = []
    for key, rules in idx.items():
        normalised = key.replace(" ", "").replace("_", "").replace("-", "")
        if needle in normalised or normalised in needle:
            results.extend(rules)
    return results


def get_all_rules() -> List[ProtocolRule]:
    """Return every protocol rule in the knowledge base."""
    return list(PROTOCOL_RULES)


def get_p0_rules(protocol_name: str) -> List[ProtocolRule]:
    """Return only P0 (must-check) rules for a protocol."""
    return [r for r in get_rules_for_protocol(protocol_name) if r.severity == "P0"]
