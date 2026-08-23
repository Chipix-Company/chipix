"""
Phase C — Protocol Detection Engine.

Auto-detects standard bus protocols from RTL signal names and generates
protocol-specific verification components.

Supported protocols:
  • AXI4 / AXI4-Lite / AXI4-Stream
  • APB
  • AHB / AHB-Lite
  • SPI
  • I2C
  • UART
  • Wishbone
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from core.logger import get_logger

logger = get_logger("ProtocolDetector")


class Protocol(str, Enum):
    """Supported bus protocols."""
    AXI4 = "AXI4"
    AXI4_LITE = "AXI4-Lite"
    AXI4_STREAM = "AXI4-Stream"
    APB = "APB"
    AHB = "AHB"
    SPI = "SPI"
    I2C = "I2C"
    UART = "UART"
    WISHBONE = "Wishbone"
    NONE = "None"


@dataclass
class ProtocolMatch:
    """Result of protocol detection for a single interface."""
    protocol: Protocol
    confidence: float = 0.0            # 0.0-1.0
    matched_signals: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)
    role: str = ""                     # "master", "slave", "both"


# ═══════════════════════════════════════════════════════════════════════
# Protocol signal patterns
# ═══════════════════════════════════════════════════════════════════════

PROTOCOL_SIGNATURES: dict[Protocol, dict] = {

    Protocol.AXI4: {
        "required": ["AWVALID", "AWREADY", "WVALID", "WREADY", "BVALID", "BREADY",
                      "ARVALID", "ARREADY", "RVALID", "RREADY"],
        "optional": ["AWADDR", "WDATA", "RDATA", "AWLEN", "AWSIZE", "AWBURST",
                      "AWID", "WID", "BID", "ARID", "RID", "WSTRB", "BRESP", "RRESP",
                      "AWLOCK", "AWCACHE", "AWPROT", "ARLEN", "ARSIZE", "ARBURST"],
        "min_required": 6,
    },

    Protocol.AXI4_LITE: {
        "required": ["AWVALID", "AWREADY", "WVALID", "WREADY", "BVALID", "BREADY",
                      "ARVALID", "ARREADY", "RVALID", "RREADY"],
        "optional": ["AWADDR", "WDATA", "RDATA", "WSTRB", "BRESP", "RRESP", "AWPROT", "ARPROT"],
        "forbidden": ["AWLEN", "AWSIZE", "AWBURST", "AWID"],  # These indicate full AXI4
        "min_required": 6,
    },

    Protocol.AXI4_STREAM: {
        "required": ["TVALID", "TREADY", "TDATA"],
        "optional": ["TSTRB", "TKEEP", "TLAST", "TID", "TDEST", "TUSER"],
        "min_required": 3,
    },

    Protocol.APB: {
        "required": ["PSEL", "PENABLE", "PWRITE", "PADDR", "PWDATA", "PRDATA", "PREADY"],
        "optional": ["PSLVERR", "PSTRB", "PPROT"],
        "min_required": 5,
    },

    Protocol.AHB: {
        "required": ["HSEL", "HTRANS", "HWRITE", "HADDR", "HWDATA", "HRDATA", "HREADY"],
        "optional": ["HSIZE", "HBURST", "HPROT", "HMASTLOCK", "HRESP", "HREADYOUT"],
        "min_required": 5,
    },

    Protocol.SPI: {
        "required": ["SCLK", "MOSI", "MISO"],
        "optional": ["SS", "CS", "SS_N", "CS_N", "CPOL", "CPHA"],
        "min_required": 3,
    },

    Protocol.I2C: {
        "required": ["SCL", "SDA"],
        "optional": ["SCL_OE", "SDA_OE", "SCL_I", "SDA_I", "SCL_O", "SDA_O"],
        "min_required": 2,
    },

    Protocol.UART: {
        "required": ["TX", "RX"],
        "optional": ["BAUD", "BAUD_CLK", "CTS", "RTS", "DTR", "DSR", "PARITY", "TX_VALID",
                      "RX_VALID", "TX_READY", "RX_READY", "TX_DATA", "RX_DATA"],
        "min_required": 2,
    },

    Protocol.WISHBONE: {
        "required": ["CYC", "STB", "ACK"],
        "optional": ["WE", "ADR", "DAT_I", "DAT_O", "SEL", "ERR", "RTY", "LOCK",
                      "CTI", "BTE"],
        "min_required": 3,
    },
}


class ProtocolDetector:
    """Auto-detect bus protocols from RTL signal names."""

    def detect(self, signal_names: list[str], rtl_code: str = "") -> list[ProtocolMatch]:
        """Detect all protocols present in the design.

        Args:
            signal_names: List of all signal/port names from RTL
            rtl_code: Full RTL code for deeper analysis

        Returns:
            List of detected protocols, sorted by confidence
        """
        # Normalize signal names (uppercase, strip prefixes)
        normalized = self._normalize_signals(signal_names)

        matches = []
        for protocol, signature in PROTOCOL_SIGNATURES.items():
            match = self._check_protocol(protocol, signature, normalized)
            if match and match.confidence > 0.3:
                matches.append(match)

        # Resolve AXI4 vs AXI4-Lite
        matches = self._resolve_axi_variants(matches)

        # Sort by confidence
        matches.sort(key=lambda m: m.confidence, reverse=True)

        if matches:
            for m in matches:
                logger.info(
                    "Detected protocol: %s (%.0f%% confidence, role=%s, signals: %s)",
                    m.protocol.value, m.confidence * 100, m.role,
                    ", ".join(m.matched_signals[:5]),
                )
        else:
            logger.info("No standard bus protocols detected")

        return matches

    def _normalize_signals(self, signal_names: list[str]) -> dict[str, str]:
        """Normalize signal names for matching.

        Strips common prefixes (s_axi_, m_axi_, axi_, apb_, etc.)
        Returns: normalized_name -> original_name mapping
        """
        prefixes = [
            "s_axi_", "m_axi_", "axi_", "s_apb_", "apb_",
            "s_ahb_", "ahb_", "wb_", "i2c_", "spi_", "uart_",
            "i_", "o_", "io_",
        ]

        mapping = {}
        for name in signal_names:
            upper = name.upper()
            # Try stripping prefixes
            stripped = upper
            for prefix in prefixes:
                if upper.startswith(prefix.upper()):
                    stripped = upper[len(prefix):]
                    break

            mapping[stripped] = name

        return mapping

    def _check_protocol(
        self,
        protocol: Protocol,
        signature: dict,
        normalized_signals: dict[str, str],
    ) -> ProtocolMatch | None:
        """Check if signals match a specific protocol."""
        required = signature["required"]
        optional = signature.get("optional", [])
        forbidden = signature.get("forbidden", [])
        min_required = signature["min_required"]

        matched = []
        missing = []

        signal_uppers = set(normalized_signals.keys())

        for sig in required:
            if sig in signal_uppers:
                matched.append(normalized_signals.get(sig, sig))
            else:
                missing.append(sig)

        # Check optional signals too
        optional_matched = []
        for sig in optional:
            if sig in signal_uppers:
                optional_matched.append(normalized_signals.get(sig, sig))

        # Check forbidden (for discriminating AXI4 vs AXI4-Lite)
        has_forbidden = False
        if forbidden:
            for sig in forbidden:
                if sig in signal_uppers:
                    has_forbidden = True
                    break

        total_matched = len(matched) + len(optional_matched)
        total_possible = len(required) + len(optional)

        if len(matched) < min_required:
            return None

        confidence = len(matched) / len(required)
        if optional_matched:
            confidence = (confidence + (len(optional_matched) / len(optional))) / 2

        if has_forbidden and protocol == Protocol.AXI4_LITE:
            return None  # It's AXI4, not Lite

        # Determine role
        role = self._detect_role(matched + optional_matched, protocol)

        return ProtocolMatch(
            protocol=protocol,
            confidence=round(confidence, 2),
            matched_signals=matched + optional_matched,
            missing_signals=missing,
            role=role,
        )

    def _detect_role(self, signals: list[str], protocol: Protocol) -> str:
        """Detect if the DUT is master, slave, or both."""
        sig_str = " ".join(s.upper() for s in signals)

        if protocol in (Protocol.AXI4, Protocol.AXI4_LITE, Protocol.AHB, Protocol.APB):
            # For bus protocols, check direction hints
            has_write = any(s in sig_str for s in ["WDATA", "PWDATA", "HWDATA"])
            has_read = any(s in sig_str for s in ["RDATA", "PRDATA", "HRDATA"])
            if has_write and has_read:
                return "both"
            elif has_write:
                return "master"
            elif has_read:
                return "slave"

        if protocol == Protocol.SPI:
            has_mosi = "MOSI" in sig_str
            has_miso = "MISO" in sig_str
            has_sclk_out = "SCLK" in sig_str  # If driving SCLK, likely master
            if has_mosi and has_miso:
                return "master" if has_sclk_out else "slave"

        return "unknown"

    def _resolve_axi_variants(self, matches: list[ProtocolMatch]) -> list[ProtocolMatch]:
        """Resolve between AXI4 and AXI4-Lite."""
        has_axi4 = any(m.protocol == Protocol.AXI4 for m in matches)
        has_lite = any(m.protocol == Protocol.AXI4_LITE for m in matches)

        if has_axi4 and has_lite:
            # Remove AXI4-Lite if full AXI4 detected (AXI4 is superset)
            return [m for m in matches if m.protocol != Protocol.AXI4_LITE]

        return matches
