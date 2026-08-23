"""
Open-Source Verification Plan (VPlan) Rules — Knowledge Base

Corner cases mined from real, production-grade verification plans in
major open-source hardware projects. Every rule traces to a specific
testpoint in a published VPlan.

Sources:
  - OpenTitan (Google/lowRISC) — UART, SPI, I2C, AES, HMAC, GPIO, TIMER, PWM, PATTGEN, SRAM_ctrl, flash_ctrl, OTP_ctrl, entropy_src, alert_handler, lifecycle_ctrl, keymgr DV plans
  - CHIPS Alliance — Caliptra Root of Trust DV plan
  - PULP Platform — CV32E40P, CVA6 verification plans
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class VPlanRule:
    """A corner case testpoint mined from an open-source verification plan.

    Attributes:
        id:               Unique identifier.
        source_project:   Origin project.
        source_ip:        Specific IP block the testpoint comes from.
        testpoint_ref:    Original testpoint name/ID in the VPlan.
        title:            Short title.
        description:      What to test.
        applicable_to:    Design categories this applies to.
        trigger_signals:  Signals for applicability.
        severity:         Priority.
        verification_approach: How to verify.
        stimulus_hint:    How to create stimulus.
        check_hint:       What to check.
    """
    id: str
    source_project: str
    source_ip: str
    testpoint_ref: str
    title: str
    description: str
    applicable_to: List[str]
    trigger_signals: List[str]
    severity: str
    verification_approach: str
    stimulus_hint: str = ""
    check_hint: str = ""


# ═══════════════════════════════════════════════════════════════════════
# OpenTitan UART DV Plan Testpoints
# ═══════════════════════════════════════════════════════════════════════

_OT_UART: List[VPlanRule] = [
    VPlanRule(
        id="VP-OT-UART-001", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_tx_rx_loopback",
        title="UART loopback mode data integrity",
        description="Enable system loopback (TX→RX internal path). Transmit known data pattern and verify received data matches exactly.",
        applicable_to=["peripheral"], trigger_signals=["uart", "tx", "rx", "loopback"],
        severity="P0", verification_approach="uvm",
        stimulus_hint="Enable loopback in CTRL register, send varying patterns.",
        check_hint="RX data == TX data for every byte.",
    ),
    VPlanRule(
        id="VP-OT-UART-002", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_tx_watermark",
        title="UART TX FIFO watermark interrupt",
        description="Configure TX watermark level. Fill TX FIFO past the watermark then drain below. Verify interrupt fires at exactly the right level.",
        applicable_to=["peripheral"], trigger_signals=["uart", "tx", "watermark", "fifo", "interrupt"],
        severity="P0", verification_approach="uvm",
        check_hint="Interrupt fires when TX FIFO level crosses watermark threshold (rising or falling per config).",
    ),
    VPlanRule(
        id="VP-OT-UART-003", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_rx_watermark",
        title="UART RX FIFO watermark interrupt",
        description="Configure RX watermark level. Receive bytes until FIFO level exceeds watermark. Verify interrupt fires.",
        applicable_to=["peripheral"], trigger_signals=["uart", "rx", "watermark", "fifo", "interrupt"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-UART-004", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_rx_break_err",
        title="UART RX break condition detection",
        description="Hold RX line low for longer than a frame duration. Verify break_err interrupt fires.",
        applicable_to=["peripheral"], trigger_signals=["uart", "rx", "break", "error"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-UART-005", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_rx_frame_err",
        title="UART RX frame error detection",
        description="Send a byte with incorrect stop bit. Verify frame_err flag is set.",
        applicable_to=["peripheral"], trigger_signals=["uart", "rx", "frame", "error", "stop"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-UART-006", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_rx_parity_err",
        title="UART RX parity error detection",
        description="Configure parity mode (odd/even). Send byte with wrong parity. Verify parity_err flag.",
        applicable_to=["peripheral"], trigger_signals=["uart", "rx", "parity", "error"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-UART-007", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_rx_overflow",
        title="UART RX FIFO overflow",
        description="Fill RX FIFO completely, then receive one more byte. Verify overflow interrupt fires and existing data is preserved.",
        applicable_to=["peripheral", "storage"], trigger_signals=["uart", "rx", "overflow", "fifo"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-UART-008", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_tx_all_baud_rates",
        title="UART TX at all supported baud rates",
        description="Transmit data at minimum, typical, and maximum baud rate settings. Verify timing accuracy of each bit.",
        applicable_to=["peripheral"], trigger_signals=["uart", "tx", "baud", "nco"],
        severity="P1", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-UART-009", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_noise_filter",
        title="UART RX noise filter rejects glitches",
        description="Enable noise filter. Inject short glitches on RX line (shorter than filter threshold). Verify they are rejected.",
        applicable_to=["peripheral"], trigger_signals=["uart", "rx", "noise", "filter", "glitch"],
        severity="P1", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-UART-010", source_project="OpenTitan", source_ip="UART",
        testpoint_ref="uart_config_while_active",
        title="UART configuration change during active transfer",
        description="Change baud rate or parity setting while a TX transfer is in progress. Verify the change takes effect after the current transfer completes, not mid-byte.",
        applicable_to=["peripheral"], trigger_signals=["uart", "config", "baud", "active", "busy"],
        severity="P0", verification_approach="uvm",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# OpenTitan SPI Host DV Plan Testpoints
# ═══════════════════════════════════════════════════════════════════════

_OT_SPI: List[VPlanRule] = [
    VPlanRule(
        id="VP-OT-SPI-001", source_project="OpenTitan", source_ip="SPI Host",
        testpoint_ref="spi_host_all_modes",
        title="SPI all CPOL/CPHA mode combinations",
        description="Run full data transfer in each of the 4 SPI modes (CPOL=0/CPHA=0, CPOL=0/CPHA=1, CPOL=1/CPHA=0, CPOL=1/CPHA=1). Verify data integrity in each.",
        applicable_to=["peripheral"], trigger_signals=["spi", "cpol", "cpha", "sclk", "mosi", "miso"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-SPI-002", source_project="OpenTitan", source_ip="SPI Host",
        testpoint_ref="spi_host_multi_segment",
        title="SPI multi-segment transaction",
        description="Execute a transaction with multiple segments (direction changes, CS hold between segments). Verify CS stays active throughout.",
        applicable_to=["peripheral"], trigger_signals=["spi", "segment", "cs", "csn"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-SPI-003", source_project="OpenTitan", source_ip="SPI Host",
        testpoint_ref="spi_host_speed_change",
        title="SPI clock speed change between transactions",
        description="Change the SPI clock divider between transactions. Verify the new speed takes effect on the next transaction.",
        applicable_to=["peripheral"], trigger_signals=["spi", "clk_div", "speed", "prescaler"],
        severity="P1", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-SPI-004", source_project="OpenTitan", source_ip="SPI Host",
        testpoint_ref="spi_host_abort",
        title="SPI abort active transaction",
        description="Abort an SPI transaction mid-transfer by software. Verify CS deasserts and the SPI engine returns to idle.",
        applicable_to=["peripheral"], trigger_signals=["spi", "abort", "active", "busy", "cs"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-SPI-005", source_project="OpenTitan", source_ip="SPI Host",
        testpoint_ref="spi_host_underflow",
        title="SPI TX underflow (FIFO starved during transfer)",
        description="Start a long SPI transfer but don't refill the TX FIFO in time. Verify the SPI engine stalls or reports error — not corrupt data.",
        applicable_to=["peripheral", "storage"], trigger_signals=["spi", "tx", "underflow", "fifo", "empty"],
        severity="P0", verification_approach="uvm",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# OpenTitan AES DV Plan Testpoints
# ═══════════════════════════════════════════════════════════════════════

_OT_AES: List[VPlanRule] = [
    VPlanRule(
        id="VP-OT-AES-001", source_project="OpenTitan", source_ip="AES",
        testpoint_ref="aes_nist_vectors",
        title="AES NIST known-answer test vectors",
        description="Run all NIST FIPS 197 test vectors (ECB, CBC, CTR, OFB, CFB modes; 128/192/256 bit keys). Verify output matches expected ciphertext.",
        applicable_to=["crypto_security"], trigger_signals=["aes", "cipher", "key", "encrypt", "decrypt"],
        severity="P0", verification_approach="unitsim",
        check_hint="Output ciphertext matches NIST expected value exactly.",
    ),
    VPlanRule(
        id="VP-OT-AES-002", source_project="OpenTitan", source_ip="AES",
        testpoint_ref="aes_key_sideload",
        title="AES key sideload from key manager",
        description="Use sideloaded key (from hardware key manager, not software-written). Run encryption and verify output matches the expected result for the sideloaded key value.",
        applicable_to=["crypto_security"], trigger_signals=["aes", "sideload", "key", "keymgr"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-AES-003", source_project="OpenTitan", source_ip="AES",
        testpoint_ref="aes_back_to_back",
        title="AES back-to-back block processing",
        description="Feed data blocks continuously without idle between them. Verify all outputs are correct and no data is lost.",
        applicable_to=["crypto_security"], trigger_signals=["aes", "block", "valid", "ready", "back_to_back"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-AES-004", source_project="OpenTitan", source_ip="AES",
        testpoint_ref="aes_clear_internal_state",
        title="AES internal state clear after operation",
        description="After an encryption/decryption operation, trigger manual clearing of internal state. Verify no residual key or data material remains in internal registers.",
        applicable_to=["crypto_security"], trigger_signals=["aes", "clear", "state", "key", "data"],
        severity="P0", verification_approach="uvm",
        check_hint="All internal state registers read as zero after clear.",
    ),
    VPlanRule(
        id="VP-OT-AES-005", source_project="OpenTitan", source_ip="AES",
        testpoint_ref="aes_prng_reseed",
        title="AES masking PRNG reseed",
        description="Trigger PRNG reseed while AES is processing. Verify the reseed completes without corrupting the current operation.",
        applicable_to=["crypto_security"], trigger_signals=["aes", "prng", "reseed", "mask", "random"],
        severity="P1", verification_approach="uvm",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# OpenTitan HMAC/SHA DV Plan Testpoints
# ═══════════════════════════════════════════════════════════════════════

_OT_HMAC: List[VPlanRule] = [
    VPlanRule(
        id="VP-OT-HMAC-001", source_project="OpenTitan", source_ip="HMAC",
        testpoint_ref="hmac_nist_vectors",
        title="HMAC NIST known-answer test vectors",
        description="Run NIST HMAC test vectors for SHA-256. Verify the digest matches the expected value.",
        applicable_to=["crypto_security"], trigger_signals=["hmac", "sha", "hash", "digest"],
        severity="P0", verification_approach="unitsim",
    ),
    VPlanRule(
        id="VP-OT-HMAC-002", source_project="OpenTitan", source_ip="HMAC",
        testpoint_ref="hmac_msg_length_boundary",
        title="HMAC message at exact block boundary",
        description="Hash a message whose length is exactly N×512 bits (block-aligned). Verify padding is applied correctly.",
        applicable_to=["crypto_security"], trigger_signals=["hmac", "sha", "length", "block", "pad"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-HMAC-003", source_project="OpenTitan", source_ip="HMAC",
        testpoint_ref="hmac_context_save_restore",
        title="HMAC context save and restore for multi-part message",
        description="Hash first part of a message, save context, hash a different message, restore context, continue hashing the original message. Verify final digest is correct.",
        applicable_to=["crypto_security"], trigger_signals=["hmac", "context", "save", "restore", "digest"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-HMAC-004", source_project="OpenTitan", source_ip="HMAC",
        testpoint_ref="hmac_zero_length_msg",
        title="HMAC hash of zero-length message",
        description="Hash a zero-length message. Verify the digest matches the known hash of empty input.",
        applicable_to=["crypto_security"], trigger_signals=["hmac", "sha", "empty", "zero", "length"],
        severity="P0", verification_approach="unitsim",
    ),
    VPlanRule(
        id="VP-OT-HMAC-005", source_project="OpenTitan", source_ip="HMAC",
        testpoint_ref="hmac_endianness",
        title="HMAC byte/word endianness configuration",
        description="Test with both big-endian and little-endian message input configurations. Verify correct digest for each.",
        applicable_to=["crypto_security"], trigger_signals=["hmac", "endian", "byte_order", "word_order"],
        severity="P1", verification_approach="uvm",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# OpenTitan GPIO, Timer, Alert Handler
# ═══════════════════════════════════════════════════════════════════════

_OT_MISC: List[VPlanRule] = [
    VPlanRule(
        id="VP-OT-GPIO-001", source_project="OpenTitan", source_ip="GPIO",
        testpoint_ref="gpio_interrupt_edge_detect",
        title="GPIO edge detection interrupt",
        description="Configure GPIO pin for rising-edge interrupt. Drive a rising edge on the pin. Verify interrupt fires exactly once.",
        applicable_to=["peripheral"], trigger_signals=["gpio", "interrupt", "edge", "rise", "fall"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-GPIO-002", source_project="OpenTitan", source_ip="GPIO",
        testpoint_ref="gpio_output_enable",
        title="GPIO output enable per-pin control",
        description="Configure specific pins as outputs, others as inputs. Verify output-enabled pins drive the correct value and input pins do not drive.",
        applicable_to=["peripheral"], trigger_signals=["gpio", "output_en", "oe", "direction"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-TMR-001", source_project="OpenTitan", source_ip="Timer",
        testpoint_ref="timer_interrupt_at_threshold",
        title="Timer interrupt fires at exact threshold",
        description="Configure timer with a specific compare value. Verify the interrupt fires exactly when the counter matches the compare value.",
        applicable_to=["peripheral"], trigger_signals=["timer", "compare", "threshold", "interrupt", "match"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-TMR-002", source_project="OpenTitan", source_ip="Timer",
        testpoint_ref="timer_prescaler_accuracy",
        title="Timer prescaler clock division accuracy",
        description="Configure timer prescaler and verify the effective timer frequency matches the expected division ratio.",
        applicable_to=["peripheral"], trigger_signals=["timer", "prescaler", "divider", "frequency"],
        severity="P1", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-ALERT-001", source_project="OpenTitan", source_ip="Alert Handler",
        testpoint_ref="alert_escalation_sequence",
        title="Alert handler escalation phase progression",
        description="Trigger a fatal alert and verify the escalation counter progresses through all configured phases (NMI → reset → wipe → terminal).",
        applicable_to=["peripheral", "crypto_security"],
        trigger_signals=["alert", "escalate", "phase", "fatal", "nmi", "reset"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-ALERT-002", source_project="OpenTitan", source_ip="Alert Handler",
        testpoint_ref="alert_ping_response",
        title="Alert handler ping mechanism",
        description="Verify each alert sender responds to the alert handler's periodic ping within the timeout. If a sender fails to respond, verify a local alert is raised.",
        applicable_to=["peripheral"], trigger_signals=["alert", "ping", "timeout", "response"],
        severity="P0", verification_approach="uvm",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# OpenTitan Flash, OTP, Entropy, SRAM Controllers
# ═══════════════════════════════════════════════════════════════════════

_OT_CONTROLLERS: List[VPlanRule] = [
    VPlanRule(
        id="VP-OT-FLASH-001", source_project="OpenTitan", source_ip="Flash Controller",
        testpoint_ref="flash_read_while_erase",
        title="Flash read blocked during erase",
        description="Issue a flash read while erase is in progress. Verify the read is either stalled or returns an error — not corrupted data.",
        applicable_to=["memory_controller"], trigger_signals=["flash", "erase", "read", "busy"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-FLASH-002", source_project="OpenTitan", source_ip="Flash Controller",
        testpoint_ref="flash_scramble_integrity",
        title="Flash data scrambling and integrity check",
        description="Write data to flash with scrambling enabled. Read it back and verify descrambling produces the original data. Inject a bit flip and verify integrity check detects it.",
        applicable_to=["memory_controller", "crypto_security"],
        trigger_signals=["flash", "scramble", "integrity", "ecc"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-OTP-001", source_project="OpenTitan", source_ip="OTP Controller",
        testpoint_ref="otp_partition_lock",
        title="OTP partition lock prevents further writes",
        description="Lock an OTP partition. Attempt to write to it. Verify the write is rejected and no modification occurs.",
        applicable_to=["memory_controller", "crypto_security"],
        trigger_signals=["otp", "partition", "lock", "write", "fuse"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-OTP-002", source_project="OpenTitan", source_ip="OTP Controller",
        testpoint_ref="otp_digest_check",
        title="OTP partition digest verification on boot",
        description="Verify the OTP controller computes and checks the digest of each locked partition on boot. Inject a mismatch and verify an alert is raised.",
        applicable_to=["memory_controller", "crypto_security"],
        trigger_signals=["otp", "digest", "check", "boot", "alert"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-ENT-001", source_project="OpenTitan", source_ip="Entropy Source",
        testpoint_ref="entropy_health_check",
        title="Entropy source health check (repetition count + adaptive proportion)",
        description="Verify the entropy source hardware health checks flag when the noise source output is biased or stuck. Inject a stuck pattern and verify the alert fires.",
        applicable_to=["crypto_security"], trigger_signals=["entropy", "health", "rng", "noise", "bias"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-ENT-002", source_project="OpenTitan", source_ip="Entropy Source",
        testpoint_ref="entropy_conditioning",
        title="Entropy conditioning (SHA-3 based)",
        description="Verify the conditioning function compresses raw entropy into full-entropy output. Check output passes NIST SP 800-90B requirements.",
        applicable_to=["crypto_security"], trigger_signals=["entropy", "condition", "sha3", "seed"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-SRAM-001", source_project="OpenTitan", source_ip="SRAM Controller",
        testpoint_ref="sram_init_on_request",
        title="SRAM initialization (wipe) on request",
        description="Trigger SRAM initialization. Verify all SRAM contents are overwritten (not just metadata) and the init-done flag is set.",
        applicable_to=["memory_controller", "crypto_security"],
        trigger_signals=["sram", "init", "wipe", "scrub", "done"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-OT-SRAM-002", source_project="OpenTitan", source_ip="SRAM Controller",
        testpoint_ref="sram_ecc_single_bit",
        title="SRAM ECC single-bit error correction",
        description="Inject a single-bit error into SRAM read data. Verify ECC corrects it transparently and logs the correction.",
        applicable_to=["memory_controller"], trigger_signals=["sram", "ecc", "single", "correct"],
        severity="P0", verification_approach="unitsim",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# PULP Platform / RISC-V Core DV Testpoints
# ═══════════════════════════════════════════════════════════════════════

_PULP_CORE: List[VPlanRule] = [
    VPlanRule(
        id="VP-PULP-001", source_project="PULP Platform", source_ip="CV32E40P",
        testpoint_ref="cv32e40p_interrupt_latency",
        title="RISC-V interrupt response latency",
        description="Assert an external interrupt and measure the number of cycles until the first instruction of the handler executes. Verify it meets the specified maximum latency.",
        applicable_to=["processor_core"], trigger_signals=["irq", "interrupt", "mtvec", "handler", "latency"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-PULP-002", source_project="PULP Platform", source_ip="CV32E40P",
        testpoint_ref="cv32e40p_compressed_all_types",
        title="RISC-V compressed instruction coverage",
        description="Execute every compressed (C-extension) instruction type. Verify each produces the same result as its 32-bit equivalent.",
        applicable_to=["processor_core"], trigger_signals=["compressed", "rvc", "c_ext", "instruction"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-PULP-003", source_project="PULP Platform", source_ip="CV32E40P",
        testpoint_ref="cv32e40p_misaligned_load_store",
        title="RISC-V misaligned load/store handling",
        description="Execute load and store instructions with misaligned addresses. Verify either correct completion (if supported) or correct exception.",
        applicable_to=["processor_core"], trigger_signals=["load", "store", "misaligned", "exception", "addr"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-PULP-004", source_project="PULP Platform", source_ip="CVA6",
        testpoint_ref="cva6_mmu_page_walk",
        title="RISC-V MMU page table walk (all page sizes)",
        description="Access addresses that require page table walks at all levels (4KB, 2MB, 1GB pages). Verify correct physical address translation.",
        applicable_to=["processor_core"], trigger_signals=["mmu", "page", "walk", "tlb", "satp"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-PULP-005", source_project="PULP Platform", source_ip="CVA6",
        testpoint_ref="cva6_cache_line_replacement",
        title="Cache line replacement policy (LRU/PLRU)",
        description="Fill the cache completely, then access a new address. Verify the evicted line is the least-recently-used line.",
        applicable_to=["processor_core", "memory_controller"],
        trigger_signals=["cache", "lru", "plru", "evict", "replace", "miss"],
        severity="P1", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-PULP-006", source_project="PULP Platform", source_ip="CVA6",
        testpoint_ref="cva6_amo_operations",
        title="RISC-V atomic memory operations (AMO)",
        description="Execute all AMO instructions (AMOSWAP, AMOADD, AMOAND, AMOOR, AMOMIN, AMOMAX, etc.). Verify the memory is updated atomically.",
        applicable_to=["processor_core"], trigger_signals=["amo", "atomic", "lr", "sc", "swap"],
        severity="P0", verification_approach="uvm",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# CHIPS Alliance — Caliptra Root of Trust
# ═══════════════════════════════════════════════════════════════════════

_CALIPTRA: List[VPlanRule] = [
    VPlanRule(
        id="VP-CAL-001", source_project="CHIPS Alliance", source_ip="Caliptra",
        testpoint_ref="caliptra_cold_boot_sequence",
        title="Caliptra cold boot ROM execution",
        description="Verify the complete cold boot sequence: ROM validation, FMC loading, RT firmware loading. Verify each stage authenticates the next.",
        applicable_to=["crypto_security", "processor_core"],
        trigger_signals=["boot", "rom", "fmc", "firmware", "authenticate", "verify"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-CAL-002", source_project="CHIPS Alliance", source_ip="Caliptra",
        testpoint_ref="caliptra_mailbox_command",
        title="Caliptra mailbox command/response protocol",
        description="Send commands via the Caliptra mailbox interface. Verify command processing, response generation, and mailbox lock/unlock sequence.",
        applicable_to=["peripheral", "crypto_security"],
        trigger_signals=["mailbox", "command", "response", "lock", "mbox"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-CAL-003", source_project="CHIPS Alliance", source_ip="Caliptra",
        testpoint_ref="caliptra_key_vault_access",
        title="Caliptra key vault access control",
        description="Verify that keys stored in the key vault are only accessible by the designated hardware engine (not by software or debug). Attempt unauthorized access and verify denial.",
        applicable_to=["crypto_security"], trigger_signals=["key_vault", "access", "deny", "engine", "protect"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-CAL-004", source_project="CHIPS Alliance", source_ip="Caliptra",
        testpoint_ref="caliptra_fuse_protection",
        title="Caliptra fuse field write protection",
        description="Verify that security-critical fuse fields cannot be overwritten after initial programming. Attempt re-programming and verify rejection.",
        applicable_to=["crypto_security", "memory_controller"],
        trigger_signals=["fuse", "otp", "protect", "lock", "program"],
        severity="P0", verification_approach="uvm",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Aggregate + Lookup
# ═══════════════════════════════════════════════════════════════════════

_GENERIC_SOC: List[VPlanRule] = [
    VPlanRule(
        id="VP-GEN-CSR-001", source_project="Generic DV", source_ip="CSR/Register Block",
        testpoint_ref="csr_reset_defaults",
        title="CSR reset default values",
        description="After reset, read every documented register/field and verify its reset value, access type, and reserved-bit behavior.",
        applicable_to=["peripheral", "bus_interconnect", "memory_controller", "crypto_security"],
        trigger_signals=["csr", "reg", "reset", "default", "status", "ctrl"],
        severity="P0", verification_approach="unitsim",
    ),
    VPlanRule(
        id="VP-GEN-CSR-002", source_project="Generic DV", source_ip="CSR/Register Block",
        testpoint_ref="csr_w1c_set_clear_race",
        title="W1C set/clear race",
        description="Clear a write-one-clear status bit while hardware asserts the same event. Verify the new event remains pending according to the precedence policy.",
        applicable_to=["peripheral", "processor_core", "crypto_security"],
        trigger_signals=["w1c", "clear", "status", "pending", "event", "irq"],
        severity="P0", verification_approach="formal",
    ),
    VPlanRule(
        id="VP-GEN-CSR-003", source_project="Generic DV", source_ip="CSR/Register Block",
        testpoint_ref="csr_read_only_write_ignore",
        title="Read-only field write ignore",
        description="Attempt writes to read-only status fields with all-zero and all-one data. Verify hardware-owned state is unchanged.",
        applicable_to=["peripheral", "memory_controller", "crypto_security"],
        trigger_signals=["ro", "read_only", "status", "wr_en", "csr"],
        severity="P1", verification_approach="unitsim",
    ),
    VPlanRule(
        id="VP-GEN-MEM-001", source_project="Generic DV", source_ip="Memory/Register File",
        testpoint_ref="byte_enable_all_patterns",
        title="Byte-enable all-pattern partial writes",
        description="For every byte-enable pattern, write a word and verify only enabled lanes update while disabled lanes preserve previous data.",
        applicable_to=["memory_controller", "storage", "peripheral"],
        trigger_signals=["byte_en", "strobe", "strb", "wstrb", "wmask", "wdata"],
        severity="P1", verification_approach="formal",
    ),
    VPlanRule(
        id="VP-GEN-IRQ-001", source_project="Generic DV", source_ip="Interrupt Logic",
        testpoint_ref="interrupt_mask_pending_behavior",
        title="Interrupt mask and pending behavior",
        description="Trigger an interrupt event while masked. Verify IRQ output stays low, pending/status is set, and IRQ fires when unmasked if still pending.",
        applicable_to=["peripheral", "processor_core", "crypto_security"],
        trigger_signals=["irq", "interrupt", "mask", "enable", "pending", "status"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-GEN-ARB-001", source_project="Generic DV", source_ip="Arbiter/Scheduler",
        testpoint_ref="arbiter_no_starvation",
        title="Arbiter no-starvation under sustained contention",
        description="Keep one or more requestors continuously active while others issue sparse requests. Verify every persistent request is eventually granted.",
        applicable_to=["arbiter_scheduler", "bus_interconnect", "network_packet"],
        trigger_signals=["request", "req", "grant", "gnt", "fair", "priority"],
        severity="P0", verification_approach="formal",
    ),
    VPlanRule(
        id="VP-GEN-CDC-001", source_project="Generic DV", source_ip="CDC Logic",
        testpoint_ref="fast_to_slow_pulse_capture",
        title="Fast-to-slow pulse capture",
        description="Send back-to-back one-cycle pulses from a fast domain into a slow domain. Verify no pulse is lost or duplicated.",
        applicable_to=["cdc", "clock_reset_pmu"],
        trigger_signals=["pulse", "toggle", "sync", "cdc", "clk_fast", "clk_slow"],
        severity="P0", verification_approach="formal",
    ),
    VPlanRule(
        id="VP-GEN-PWR-001", source_project="Generic DV", source_ip="Power/Clock Control",
        testpoint_ref="wakeup_during_sleep_entry",
        title="Wakeup during sleep-entry race",
        description="Assert wakeup while the design is entering low power. Verify the wakeup is not lost and the power controller exits to a legal active state.",
        applicable_to=["clock_reset_pmu", "peripheral", "processor_core"],
        trigger_signals=["wakeup", "sleep", "power_down", "idle", "pwr_req", "pwr_ack"],
        severity="P0", verification_approach="uvm",
    ),
    VPlanRule(
        id="VP-GEN-SEC-001", source_project="Generic DV", source_ip="Security/Debug Control",
        testpoint_ref="debug_locked_in_production",
        title="Debug/test access locked in production mode",
        description="Enable production/locked lifecycle state and attempt debug, scan, or test access. Verify privileged paths are denied.",
        applicable_to=["crypto_security", "processor_core", "test_debug"],
        trigger_signals=["debug", "dbg", "scan", "test_mode", "jtag", "lock", "lifecycle"],
        severity="P0", verification_approach="uvm",
    ),
]

VPLAN_RULES: List[VPlanRule] = (
    _OT_UART
    + _OT_SPI
    + _OT_AES
    + _OT_HMAC
    + _OT_MISC
    + _OT_CONTROLLERS
    + _PULP_CORE
    + _CALIPTRA
    + _GENERIC_SOC
)

_PROJECT_INDEX: Dict[str, List[VPlanRule]] = {}


def _ensure_vplan_index() -> Dict[str, List[VPlanRule]]:
    if not _PROJECT_INDEX:
        for r in VPLAN_RULES:
            key = r.source_project.lower()
            _PROJECT_INDEX.setdefault(key, []).append(r)
    return _PROJECT_INDEX


def get_all_vplan_rules() -> List[VPlanRule]:
    """Return all VPlan-mined rules."""
    return list(VPLAN_RULES)


def get_vplan_rules_by_project(project: str) -> List[VPlanRule]:
    """Return VPlan rules from a specific project."""
    idx = _ensure_vplan_index()
    return list(idx.get(project.lower(), []))


def get_vplan_rules_for_category(category: str) -> List[VPlanRule]:
    """Return all VPlan rules applicable to a design category."""
    cat_lower = category.lower()
    return [r for r in VPLAN_RULES if cat_lower in [c.lower() for c in r.applicable_to]]
