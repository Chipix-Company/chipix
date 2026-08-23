"""
Phase C — Protocol Knowledge Base.

Pre-built rule sets, assertion templates, and verification guidance
for each supported bus protocol. Fed as additional context to the LLM
when a protocol is detected.

For each protocol:
  • Signal descriptions and timing requirements
  • Common assertion templates (SVA)
  • Key verification scenarios
  • Common bugs and pitfalls
"""

from __future__ import annotations

from core.protocol_detector import Protocol
from core.logger import get_logger

logger = get_logger("ProtocolRules")


PROTOCOL_KNOWLEDGE: dict[Protocol, dict] = {

    # ═══════════════════════════════════════════════════════════════════
    # AXI4
    # ═══════════════════════════════════════════════════════════════════
    Protocol.AXI4: {
        "description": "AMBA AXI4 high-performance bus protocol",

        "timing_rules": [
            "VALID must not depend on READY (no combinational loop)",
            "Once VALID is asserted, it must stay high until READY is asserted",
            "Data must be stable while VALID is high and READY is low",
            "Write response must come after last write data beat (WLAST)",
            "Read data beats must arrive in order for a given ID",
            "AWLEN+1 = number of data beats in a burst",
        ],

        "assertion_templates": [
            # Handshake stability
            "property p_aw_stable; @(posedge ACLK) disable iff(!ARESETn) AWVALID && !AWREADY |=> AWVALID; endproperty",
            "property p_w_stable;  @(posedge ACLK) disable iff(!ARESETn) WVALID && !WREADY |=> WVALID; endproperty",
            "property p_ar_stable; @(posedge ACLK) disable iff(!ARESETn) ARVALID && !ARREADY |=> ARVALID; endproperty",
            "property p_r_stable;  @(posedge ACLK) disable iff(!ARESETn) RVALID && !RREADY |=> RVALID; endproperty",
            "property p_b_stable;  @(posedge ACLK) disable iff(!ARESETn) BVALID && !BREADY |=> BVALID; endproperty",
            # No X on valid
            "property p_no_x_awaddr; @(posedge ACLK) AWVALID |-> !$isunknown(AWADDR); endproperty",
            "property p_no_x_wdata;  @(posedge ACLK) WVALID |-> !$isunknown(WDATA); endproperty",
            # Reset behavior
            "property p_reset_aw; @(posedge ACLK) !ARESETn |-> !AWVALID; endproperty",
            "property p_reset_w;  @(posedge ACLK) !ARESETn |-> !WVALID; endproperty",
            "property p_reset_ar; @(posedge ACLK) !ARESETn |-> !ARVALID; endproperty",
        ],

        "scenarios": [
            "Single write transaction",
            "Single read transaction",
            "Burst write (INCR, WRAP, FIXED)",
            "Burst read (INCR, WRAP, FIXED)",
            "Outstanding transactions (multiple IDs)",
            "Back-to-back transactions",
            "Interleaved read/write",
            "Reset during transaction",
            "Narrow transfers (WSTRB partial)",
            "Maximum burst length",
        ],

        "pitfalls": [
            "VALID depending on READY (protocol violation)",
            "Missing WLAST on final beat",
            "Incorrect burst length calculation",
            "Address wrapping errors for WRAP bursts",
            "ID ordering violations",
        ],
    },

    # ═══════════════════════════════════════════════════════════════════
    # APB
    # ═══════════════════════════════════════════════════════════════════
    Protocol.APB: {
        "description": "AMBA APB low-power peripheral bus protocol",

        "timing_rules": [
            "Setup phase: PSEL=1, PENABLE=0 for 1 cycle",
            "Access phase: PSEL=1, PENABLE=1, wait for PREADY",
            "PADDR, PWRITE, PWDATA must be stable during transfer",
            "PREADY can be deasserted to insert wait states",
            "PSLVERR is sampled on the last cycle (PSEL & PENABLE & PREADY)",
        ],

        "assertion_templates": [
            "property p_apb_setup; @(posedge PCLK) disable iff(!PRESETn) $rose(PSEL) |-> !PENABLE; endproperty",
            "property p_apb_enable; @(posedge PCLK) disable iff(!PRESETn) PSEL && !PENABLE |=> PENABLE; endproperty",
            "property p_apb_stable_addr; @(posedge PCLK) disable iff(!PRESETn) PSEL && !PENABLE |=> $stable(PADDR); endproperty",
            "property p_apb_stable_write; @(posedge PCLK) disable iff(!PRESETn) PSEL && !PENABLE |=> $stable(PWRITE); endproperty",
            "property p_apb_deassert; @(posedge PCLK) disable iff(!PRESETn) PSEL && PENABLE && PREADY |=> !PENABLE || !PSEL; endproperty",
        ],

        "scenarios": [
            "Register write and read-back",
            "Write followed by read (same address)",
            "Wait state insertion (PREADY low)",
            "Error response (PSLVERR)",
            "Back-to-back transfers",
            "Reset during transfer",
            "All addresses access",
        ],

        "pitfalls": [
            "PENABLE asserted in setup phase",
            "Data not stable between setup and access phase",
            "PREADY not properly handled (infinite wait)",
            "Missing PSLVERR check",
        ],
    },

    # ═══════════════════════════════════════════════════════════════════
    # SPI
    # ═══════════════════════════════════════════════════════════════════
    Protocol.SPI: {
        "description": "Serial Peripheral Interface",

        "timing_rules": [
            "CPOL=0: SCLK idle low; CPOL=1: SCLK idle high",
            "CPHA=0: sample on leading edge; CPHA=1: sample on trailing edge",
            "CS/SS must be asserted (low) before and during transfer",
            "MSB first (typically) or LSB first (check config)",
            "Data changes on opposite clock edge from sampling",
        ],

        "assertion_templates": [
            "property p_spi_cs_active; @(posedge SCLK) !CS_N |-> ##[1:$] $rose(CS_N); endproperty",
            "property p_spi_mosi_stable; @(posedge SCLK) !CS_N |-> !$isunknown(MOSI); endproperty",
            "property p_spi_idle; @(posedge clk) CS_N |-> (SCLK == CPOL); endproperty",
        ],

        "scenarios": [
            "Single byte transfer",
            "Multi-byte transfer",
            "All SPI modes (CPOL/CPHA: 0/0, 0/1, 1/0, 1/1)",
            "CS toggle between bytes",
            "Maximum clock frequency",
            "Slave select with multiple slaves",
        ],

        "pitfalls": [
            "Wrong CPOL/CPHA mode",
            "Setup/hold time violations on data",
            "CS not properly framing transfer",
            "Clock frequency too high for slave",
        ],
    },

    # ═══════════════════════════════════════════════════════════════════
    # I2C
    # ═══════════════════════════════════════════════════════════════════
    Protocol.I2C: {
        "description": "Inter-Integrated Circuit (I2C/IIC) protocol",

        "timing_rules": [
            "START condition: SDA falls while SCL is high",
            "STOP condition: SDA rises while SCL is high",
            "Data valid: SDA stable while SCL is high",
            "SDA changes only when SCL is low",
            "ACK/NACK: 9th clock cycle, receiver pulls SDA low = ACK",
            "Clock stretching: slave holds SCL low to pause",
        ],

        "assertion_templates": [
            "property p_i2c_start; @(negedge SDA) SCL |-> ##1 !SCL; endproperty",
            "property p_i2c_stop; @(posedge SDA) SCL; endproperty",
            "property p_i2c_data_stable; @(posedge SCL) !$isunknown(SDA); endproperty",
        ],

        "scenarios": [
            "Write 1 byte to slave",
            "Read 1 byte from slave",
            "Multi-byte write",
            "Multi-byte read",
            "Repeated START",
            "NACK from slave",
            "Clock stretching",
            "Address matching (7-bit and 10-bit)",
            "Multi-master arbitration",
        ],

        "pitfalls": [
            "Glitches on SDA during SCL high",
            "Missing ACK handling",
            "Clock stretching not supported",
            "Open-drain not properly modeled",
        ],
    },

    # ═══════════════════════════════════════════════════════════════════
    # UART
    # ═══════════════════════════════════════════════════════════════════
    Protocol.UART: {
        "description": "Universal Asynchronous Receiver Transmitter",

        "timing_rules": [
            "Idle state: line high (mark)",
            "START bit: line pulled low for 1 bit period",
            "Data bits: LSB first (typically 8 bits)",
            "Parity bit (optional): even or odd",
            "STOP bit(s): line high for 1 or 2 bit periods",
            "Baud rate determines bit timing",
        ],

        "assertion_templates": [
            "property p_uart_idle; @(posedge clk) !tx_busy |-> TX == 1'b1; endproperty",
            "property p_uart_start; @(posedge clk) $fell(TX) && !tx_busy |-> ##1 tx_busy; endproperty",
            "property p_uart_frame; @(posedge clk) $fell(TX) |-> ##(BITS_PER_FRAME) TX; endproperty",
        ],

        "scenarios": [
            "Send single character",
            "Receive single character",
            "Back-to-back characters",
            "Different baud rates (9600, 115200, etc.)",
            "Parity error injection",
            "Frame error (missing stop bit)",
            "Overrun error",
            "RTS/CTS flow control",
        ],

        "pitfalls": [
            "Baud rate mismatch between TX and RX",
            "Incorrect parity calculation",
            "Missing stop bit detection",
            "FIFO overrun not handled",
        ],
    },

    # ═══════════════════════════════════════════════════════════════════
    # AHB
    # ═══════════════════════════════════════════════════════════════════
    Protocol.AHB: {
        "description": "AMBA AHB (Advanced High-performance Bus)",

        "timing_rules": [
            "Address phase: HTRANS, HADDR, HWRITE driven by master",
            "Data phase: HWDATA (write) or HRDATA (read) on next cycle",
            "HREADY controls pipeline: low inserts wait states",
            "HTRANS: IDLE(00), BUSY(01), NONSEQ(10), SEQ(11)",
            "Pipeline: address phase of next overlaps data phase of current",
        ],

        "assertion_templates": [
            "property p_ahb_idle_reset; @(posedge HCLK) !HRESETn |-> HTRANS == 2'b00; endproperty",
            "property p_ahb_stable; @(posedge HCLK) (HTRANS != 2'b00) && !HREADY |=> $stable(HADDR); endproperty",
        ],

        "scenarios": [
            "Single read/write",
            "Burst transfers (INCR, WRAP4, WRAP8, WRAP16)",
            "Wait state insertion",
            "Error response (HRESP)",
            "Pipeline stalls",
            "Split/retry (AHB full)",
            "Idle/busy transfers",
        ],

        "pitfalls": [
            "Pipeline not properly handled",
            "BUSY transfer timing wrong",
            "WRAP burst address calculation",
            "HREADY/HREADYOUT confusion",
        ],
    },

    Protocol.WISHBONE: {
        "description": "Wishbone open-source bus protocol",

        "timing_rules": [
            "Classic cycle: CYC & STB asserted, wait for ACK",
            "Pipelined: multiple outstanding requests",
            "ERR and RTY are error and retry signals",
        ],

        "assertion_templates": [
            "property p_wb_ack; @(posedge CLK) CYC && STB |-> ##[1:$] ACK || ERR || RTY; endproperty",
        ],

        "scenarios": [
            "Single read/write",
            "Block transfer",
            "Error response",
            "Retry response",
        ],

        "pitfalls": [
            "CYC not properly framing transfers",
            "Missing timeout on ACK",
        ],
    },
}


def get_protocol_context(protocol: Protocol) -> str:
    """Get the full protocol knowledge block for LLM context injection.

    Returns a formatted string with timing rules, assertions, scenarios,
    and pitfalls for the given protocol.
    """
    knowledge = PROTOCOL_KNOWLEDGE.get(protocol)
    if not knowledge:
        return ""

    lines = [
        f"=== PROTOCOL KNOWLEDGE: {protocol.value} ===",
        f"Description: {knowledge['description']}",
        "",
        "TIMING RULES:",
    ]
    for rule in knowledge["timing_rules"]:
        lines.append(f"  - {rule}")

    lines.append("")
    lines.append("SVA ASSERTION TEMPLATES:")
    for assertion in knowledge["assertion_templates"]:
        lines.append(f"  {assertion}")

    lines.append("")
    lines.append("KEY VERIFICATION SCENARIOS:")
    for scenario in knowledge["scenarios"]:
        lines.append(f"  - {scenario}")

    lines.append("")
    lines.append("COMMON PITFALLS:")
    for pitfall in knowledge["pitfalls"]:
        lines.append(f"  - {pitfall}")

    lines.append(f"=== END PROTOCOL: {protocol.value} ===")

    return "\n".join(lines)


def get_all_detected_context(detected_protocols: list) -> str:
    """Get combined protocol context for all detected protocols."""
    blocks = []
    for match in detected_protocols:
        ctx = get_protocol_context(match.protocol)
        if ctx:
            blocks.append(ctx)
    return "\n\n".join(blocks)
