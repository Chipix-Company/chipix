"""
Silicon Errata & Known Bug Patterns — Knowledge Base

Rules derived from real silicon bugs found in shipped chips and
documented in public errata sheets, GitHub issues, and the MITRE CWE
Hardware Weakness Enumeration.

Sources:
  - ARM Cortex-M/A/R published errata documents
  - Intel x86 specification updates
  - RISC-V ISA errata (riscv-isa-manual GitHub issues)
  - OpenTitan GitHub issues (silicon-quality bugs)
  - MITRE CWE (hardware-specific weaknesses)
  - CHIPS Alliance Caliptra security bug reports
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ErrataRule:
    """A rule derived from a real silicon bug or documented hardware weakness.

    Attributes:
        id:               Unique identifier (e.g. ``"ERRATA-ARM-001"``).
        source_family:    Origin family of the errata.
        errata_ref:       Original errata ID or CWE number.
        title:            Short descriptive title.
        description:      What to test to catch this class of bug.
        affected_category:Design categories this errata applies to.
        trigger_signals:  Signal patterns that indicate applicability.
        severity:         ``"P0"`` | ``"P1"`` | ``"P2"``.
        corner_type:      Functional category.
        verification_approach: How to verify.
        root_cause:       What caused the original bug.
        fix_pattern:      How the bug was typically fixed.
    """
    id: str
    source_family: str
    errata_ref: str
    title: str
    description: str
    affected_category: List[str]
    trigger_signals: List[str]
    severity: str
    corner_type: str
    verification_approach: str
    root_cause: str
    fix_pattern: str = ""


# ═══════════════════════════════════════════════════════════════════════
# ARM Cortex Errata Patterns
# ═══════════════════════════════════════════════════════════════════════

_ARM_ERRATA: List[ErrataRule] = [
    ErrataRule(
        id="ERRATA-ARM-001", source_family="ARM Cortex-M",
        errata_ref="Cortex-M4 Errata 838869",
        title="Store immediate overlapping exception return",
        description="Execute a store immediate instruction that completes simultaneously with an exception return. Verify the store target contains the correct value and the return address is not corrupted.",
        affected_category=["processor_core"],
        trigger_signals=["store", "exception", "return", "stm", "str", "pc"],
        severity="P0", corner_type="hazard",
        verification_approach="uvm",
        root_cause="Pipeline flush during store writeback allowed partial commit of store data alongside exception return restoration.",
    ),
    ErrataRule(
        id="ERRATA-ARM-002", source_family="ARM Cortex-M",
        errata_ref="Cortex-M7 Errata 1259864",
        title="LDRD/STRD crossing MPU boundary",
        description="Issue a double-word load/store that crosses an MPU region boundary. Verify that the correct access permissions are applied to each half and that a fault is raised if either half violates permissions.",
        affected_category=["processor_core"],
        trigger_signals=["ldrd", "strd", "mpu", "region", "boundary", "fault"],
        severity="P0", corner_type="boundary",
        verification_approach="uvm",
        root_cause="MPU check was applied only to the first word's address, not the second.",
    ),
    ErrataRule(
        id="ERRATA-ARM-003", source_family="ARM Cortex-M",
        errata_ref="Cortex-M4 Errata 752770",
        title="Interrupted multi-cycle multiply incorrect result",
        description="A multi-cycle multiply instruction interrupted by a higher-priority exception may produce an incorrect result when resumed. Verify multiply results when interrupts fire during execution.",
        affected_category=["processor_core"],
        trigger_signals=["multiply", "mul", "irq", "interrupt", "stall"],
        severity="P0", corner_type="hazard",
        verification_approach="uvm",
        root_cause="Partial multiply state was not saved/restored correctly across the interrupt.",
    ),
    ErrataRule(
        id="ERRATA-ARM-004", source_family="ARM Cortex-A",
        errata_ref="Cortex-A53 Errata 835769",
        title="Multiply-accumulate followed by specific store sequence",
        description="A multiply-accumulate instruction followed by a specific sequence of stores may produce incorrect results. Test sequential MLA/MLS followed by STR/STP patterns.",
        affected_category=["processor_core"],
        trigger_signals=["mla", "mls", "madd", "msub", "store", "stp"],
        severity="P0", corner_type="hazard",
        verification_approach="uvm",
        root_cause="Forwarding path from multiply-accumulate to store address calculation had timing issue.",
    ),
    ErrataRule(
        id="ERRATA-ARM-005", source_family="ARM Cortex-A",
        errata_ref="Cortex-A57 Errata 852523",
        title="Speculative load from device memory region",
        description="Processor may speculatively issue a load from a memory region marked as Device type. Verify no speculative accesses occur to device memory (side-effect free guarantee).",
        affected_category=["processor_core"],
        trigger_signals=["load", "device", "memory", "speculative", "mmu", "attribute"],
        severity="P0", corner_type="hazard",
        verification_approach="formal",
        root_cause="Speculative execution engine did not consult MMU attributes before issuing prefetch.",
    ),
    ErrataRule(
        id="ERRATA-ARM-006", source_family="ARM Cortex-M",
        errata_ref="Cortex-M33 Errata 2356587",
        title="Incorrect privilege check on stack limit",
        description="Stack limit check may use incorrect privilege level when exception entry changes privilege. Verify stack limit enforcement across privilege transitions.",
        affected_category=["processor_core"],
        trigger_signals=["stack", "sp", "limit", "privilege", "exception", "msp", "psp"],
        severity="P0", corner_type="security",
        verification_approach="uvm",
        root_cause="Stack limit comparison used pre-exception privilege instead of post-exception privilege.",
    ),
    ErrataRule(
        id="ERRATA-ARM-007", source_family="ARM Cortex-R",
        errata_ref="Cortex-R5 Errata 780125",
        title="ECC error during cache linefill with concurrent write",
        description="An ECC error detected during a cache linefill that overlaps with a store to the same line may cause data corruption. Test concurrent cache fill + store to same address.",
        affected_category=["processor_core", "memory_controller"],
        trigger_signals=["ecc", "cache", "linefill", "store", "write", "hit"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="ECC correction and store merge logic had a race condition on the same cache way.",
    ),
    ErrataRule(
        id="ERRATA-ARM-008", source_family="ARM Cortex-A",
        errata_ref="Cortex-A72 Errata 853709",
        title="Wrong branch prediction after ERET",
        description="Branch predictor may use stale entries after exception return (ERET). Verify branch targets are correct after returning from exception handler.",
        affected_category=["processor_core"],
        trigger_signals=["branch", "predict", "btb", "eret", "exception", "return"],
        severity="P1", corner_type="hazard",
        verification_approach="uvm",
        root_cause="BTB was not invalidated on privilege-level change via ERET.",
    ),
    ErrataRule(
        id="ERRATA-ARM-009", source_family="ARM Cortex-M",
        errata_ref="Cortex-M0+ Errata 602117",
        title="WFI followed by pending interrupt does not wake",
        description="If WFI is executed and an interrupt becomes pending on the same cycle, the core may not wake up. Verify WFI wake-up with simultaneous interrupt assertion.",
        affected_category=["processor_core"],
        trigger_signals=["wfi", "wfe", "sleep", "interrupt", "pending", "wake"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="Sleep entry logic sampled pending status one cycle before the interrupt controller updated it.",
    ),
    ErrataRule(
        id="ERRATA-ARM-010", source_family="ARM Cortex-A",
        errata_ref="Cortex-A76 Errata 1286807",
        title="TLB conflict during concurrent page table walk",
        description="Two concurrent page table walks resolving to the same TLB entry may cause a TLB conflict exception or silent data corruption. Test with multiple cores accessing shared pages.",
        affected_category=["processor_core"],
        trigger_signals=["tlb", "walk", "page", "mmu", "conflict", "translation"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="TLB allocation logic did not handle simultaneous allocations to the same index.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# RISC-V ISA Errata and Known Issues
# ═══════════════════════════════════════════════════════════════════════

_RISCV_ERRATA: List[ErrataRule] = [
    ErrataRule(
        id="ERRATA-RV-001", source_family="RISC-V",
        errata_ref="riscv-isa-manual Issue #543",
        title="MRET with pending interrupt and MIE=0",
        description="Execute MRET while interrupts are globally disabled (MIE=0) but an interrupt is pending. Verify MIE is restored from MPIE and the pending interrupt is taken at the correct time.",
        affected_category=["processor_core"],
        trigger_signals=["mret", "mstatus", "mie", "mpie", "pending", "interrupt"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="MPIE→MIE restore and pending interrupt check had one-cycle race.",
    ),
    ErrataRule(
        id="ERRATA-RV-002", source_family="RISC-V",
        errata_ref="RISC-V Privileged Spec clarification",
        title="SFENCE.VMA ordering with subsequent loads",
        description="After SFENCE.VMA, subsequent loads must use the new page table entries. Verify no stale TLB entries are used after the fence.",
        affected_category=["processor_core"],
        trigger_signals=["sfence", "vma", "tlb", "fence", "load", "page"],
        severity="P0", corner_type="ordering",
        verification_approach="uvm",
        root_cause="TLB invalidation was posted (async) — loads in the pipeline used stale entries.",
    ),
    ErrataRule(
        id="ERRATA-RV-003", source_family="RISC-V",
        errata_ref="CV32E40P GitHub Issue #465",
        title="Division by zero with subsequent instruction dependency",
        description="Execute a division by zero followed immediately by an instruction that reads the result register. Verify the result is the architecturally-defined value (all 1s for unsigned, -1 for signed).",
        affected_category=["processor_core"],
        trigger_signals=["div", "divu", "rem", "remu", "zero", "rd"],
        severity="P0", corner_type="hazard",
        verification_approach="uvm",
        root_cause="Division-by-zero fast-path bypassed the result forwarding network.",
    ),
    ErrataRule(
        id="ERRATA-RV-004", source_family="RISC-V",
        errata_ref="CVA6 GitHub Issue #782",
        title="Misaligned AMO instruction exception priority",
        description="An AMO instruction with a misaligned address should raise a misaligned exception, not an access fault. Verify exception priority when AMO address is unaligned.",
        affected_category=["processor_core"],
        trigger_signals=["amo", "atomic", "misaligned", "exception", "fault", "addr"],
        severity="P1", corner_type="error",
        verification_approach="uvm",
        root_cause="Access fault check was performed before alignment check in the load-store unit.",
    ),
    ErrataRule(
        id="ERRATA-RV-005", source_family="RISC-V",
        errata_ref="SweRV EH1 GitHub Issue #84",
        title="CSR write followed by CSR read in same cycle (bypass)",
        description="Write to a CSR followed immediately by a read of the same CSR. Verify the read returns the just-written value via forwarding.",
        affected_category=["processor_core"],
        trigger_signals=["csr", "csrw", "csrr", "csrrw", "csrrs", "csrrc"],
        severity="P0", corner_type="hazard",
        verification_approach="uvm",
        root_cause="CSR write result was available one cycle later than the bypass path expected.",
    ),
    ErrataRule(
        id="ERRATA-RV-006", source_family="RISC-V",
        errata_ref="CV32E40P GitHub Issue #512",
        title="Hardware loop end-of-loop coincides with interrupt",
        description="If a hardware loop's last iteration coincides with an interrupt, the loop counter may not be correctly saved/restored. Verify loop continues correctly after interrupt return.",
        affected_category=["processor_core"],
        trigger_signals=["loop", "hwloop", "lpcount", "lpstart", "lpend", "interrupt"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="Hardware loop state (counter, start/end) was not part of the interrupt context save.",
    ),
    ErrataRule(
        id="ERRATA-RV-007", source_family="RISC-V",
        errata_ref="CVA6 GitHub Issue #901",
        title="Store buffer forwarding with partial overlap",
        description="A load that partially overlaps with a pending store in the store buffer may get incorrect data (partial forwarding). Verify byte-level forwarding correctness.",
        affected_category=["processor_core"],
        trigger_signals=["store", "load", "buffer", "forward", "partial", "byte"],
        severity="P0", corner_type="hazard",
        verification_approach="uvm",
        root_cause="Store buffer forwarding only checked full-word overlap, not byte-level.",
    ),
    ErrataRule(
        id="ERRATA-RV-008", source_family="RISC-V",
        errata_ref="RISC-V Debug Spec Issue #204",
        title="Debug single-step across privilege boundary",
        description="Single-stepping through an instruction that causes a privilege change (ECALL, MRET). Verify the debugger halts at the correct PC in the new privilege level.",
        affected_category=["processor_core", "test_debug"],
        trigger_signals=["debug", "step", "single_step", "ecall", "mret", "privilege"],
        severity="P1", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="Single-step trigger was checked before privilege-level update, halting at wrong PC.",
    ),
    ErrataRule(
        id="ERRATA-RV-009", source_family="RISC-V",
        errata_ref="CV32E40P GitHub Issue #378",
        title="Compressed instruction fetch at page boundary",
        description="A 16-bit compressed instruction at the last 2 bytes of a page may cause a spurious page fault on the next page if the fetch unit reads ahead. Verify no false fault.",
        affected_category=["processor_core"],
        trigger_signals=["compressed", "fetch", "page", "boundary", "fault", "pc"],
        severity="P1", corner_type="boundary",
        verification_approach="uvm",
        root_cause="Instruction fetch unit always fetched 32 bits, crossing into next page for 16-bit instruction.",
    ),
    ErrataRule(
        id="ERRATA-RV-010", source_family="RISC-V",
        errata_ref="SweRV EL2 Known Issue",
        title="Icache parity error during branch prediction",
        description="If the instruction cache detects a parity error on a line that was accessed via branch prediction (speculative), the error may be reported as a real fault. Verify speculative parity errors are suppressed.",
        affected_category=["processor_core"],
        trigger_signals=["icache", "parity", "branch", "predict", "speculative", "error"],
        severity="P1", corner_type="error",
        verification_approach="uvm",
        root_cause="Parity error reporting did not distinguish speculative from committed accesses.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# OpenTitan Silicon Bug Patterns
# ═══════════════════════════════════════════════════════════════════════

_OPENTITAN_ERRATA: List[ErrataRule] = [
    ErrataRule(
        id="ERRATA-OT-001", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #12847",
        title="AES key sideload race with software key write",
        description="If software writes a new key while sideload key is being latched, the AES engine may use a mixed key. Verify key source arbitration under concurrent access.",
        affected_category=["crypto_security"],
        trigger_signals=["key", "sideload", "sw_key", "key_valid", "key_sel"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="Key multiplexer select changed mid-latch without proper handshake.",
    ),
    ErrataRule(
        id="ERRATA-OT-002", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #8234",
        title="HMAC message padding at exact block boundary",
        description="When the message length is an exact multiple of the SHA block size (512 bits), the padding logic may produce an incorrect extra block. Verify hash output for block-aligned messages.",
        affected_category=["crypto_security"],
        trigger_signals=["hmac", "sha", "hash", "pad", "block", "length"],
        severity="P0", corner_type="boundary",
        verification_approach="unitsim",
        root_cause="Padding state machine treated exact-block-boundary as needing an extra byte of padding.",
    ),
    ErrataRule(
        id="ERRATA-OT-003", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #10521",
        title="Flash controller read during erase operation",
        description="Issuing a flash read while an erase operation is in progress may return corrupted data without setting an error flag. Verify read-during-erase is properly blocked or returns error.",
        affected_category=["memory_controller", "peripheral"],
        trigger_signals=["flash", "erase", "read", "busy", "error"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="Erase-busy check was in a different clock domain from the read path — missed by one cycle.",
    ),
    ErrataRule(
        id="ERRATA-OT-004", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #14102",
        title="Entropy source health check false positive after reset",
        description="Immediately after reset, the entropy source health checker may trigger a false alarm because the statistical counters start from zero. Verify health check is suppressed during initial seed collection.",
        affected_category=["crypto_security"],
        trigger_signals=["entropy", "health", "alert", "rng", "seed", "reset"],
        severity="P1", corner_type="reset",
        verification_approach="uvm",
        root_cause="Health check thresholds were applied before the sliding window had enough samples.",
    ),
    ErrataRule(
        id="ERRATA-OT-005", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #9876",
        title="Alert handler escalation counter wrap",
        description="If the escalation phase counter wraps around (e.g. 16-bit counter at 65535 cycles), the escalation may restart from phase 0 instead of staying in the terminal phase.",
        affected_category=["peripheral"],
        trigger_signals=["alert", "escalate", "counter", "phase", "terminal"],
        severity="P0", corner_type="boundary",
        verification_approach="formal",
        root_cause="Counter wrap was not saturated — rolled over to zero and re-entered phase 0.",
    ),
    ErrataRule(
        id="ERRATA-OT-006", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #11234",
        title="OTP controller read collision with integrity check",
        description="A software-initiated OTP read that collides with the background integrity check may return the integrity check's data instead. Verify read data correctness during background checks.",
        affected_category=["memory_controller", "peripheral"],
        trigger_signals=["otp", "read", "integrity", "check", "background", "collision"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="OTP memory port was shared without proper arbitration between SW reads and HW integrity checks.",
    ),
    ErrataRule(
        id="ERRATA-OT-007", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #13567",
        title="SPI host CPHA=1 last bit sampling",
        description="In SPI CPHA=1 mode, the last bit of a transfer may be sampled at the wrong clock edge. Verify all CPOL/CPHA mode combinations with full data pattern coverage.",
        affected_category=["peripheral"],
        trigger_signals=["spi", "cpha", "cpol", "sclk", "miso", "mosi", "last"],
        severity="P0", corner_type="timing",
        verification_approach="uvm",
        root_cause="Clock edge selection logic had off-by-one error in the terminal bit counter.",
    ),
    ErrataRule(
        id="ERRATA-OT-008", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #7890",
        title="UART RX break detection length",
        description="Break condition detection may trigger for a pause shorter than the configured break length if the RX line transitions during the sampling window edge.",
        affected_category=["peripheral"],
        trigger_signals=["uart", "rx", "break", "detect", "length"],
        severity="P1", corner_type="timing",
        verification_approach="uvm",
        root_cause="Break counter was reset on the raw RX pin instead of the synchronized version.",
    ),
    ErrataRule(
        id="ERRATA-OT-009", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #15432",
        title="Lifecycle controller state transition during reset",
        description="If reset occurs during a lifecycle state transition, the OTP may contain a partially programmed state. Verify lifecycle state integrity after reset during transition.",
        affected_category=["crypto_security", "peripheral"],
        trigger_signals=["lifecycle", "lc", "state", "transition", "otp", "reset"],
        severity="P0", corner_type="reset",
        verification_approach="uvm",
        root_cause="Lifecycle OTP write was not atomic — reset mid-write left partial state.",
    ),
    ErrataRule(
        id="ERRATA-OT-010", source_family="OpenTitan",
        errata_ref="OpenTitan Issue #16001",
        title="Key manager advance during sideload operation",
        description="Advancing the key manager state while a sideload key is being consumed by a peripheral may cause the peripheral to receive a key from the wrong state.",
        affected_category=["crypto_security"],
        trigger_signals=["keymgr", "advance", "sideload", "key", "state", "valid"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="Key output buffer was updated before the consumer acknowledged the current key.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# MITRE CWE Hardware Weakness Patterns
# ═══════════════════════════════════════════════════════════════════════

_CWE_HARDWARE: List[ErrataRule] = [
    ErrataRule(
        id="CWE-HW-001", source_family="MITRE CWE",
        errata_ref="CWE-1191",
        title="On-chip debug/test interface with improper access control",
        description="Verify that debug interfaces (JTAG, SWD) are properly locked in production lifecycle states. Test that debug access is denied after lockdown.",
        affected_category=["test_debug", "crypto_security"],
        trigger_signals=["debug", "jtag", "swd", "lock", "enable", "lifecycle"],
        severity="P0", corner_type="security",
        verification_approach="uvm",
        root_cause="Debug port remained accessible after lifecycle transition to production mode.",
        fix_pattern="Gate debug enable with lifecycle state and fuse bits.",
    ),
    ErrataRule(
        id="CWE-HW-002", source_family="MITRE CWE",
        errata_ref="CWE-1233",
        title="Security-sensitive hardware controls with missing lock bits",
        description="Critical configuration registers (security policy, access control, key material) must have lock bits that prevent modification after initial configuration.",
        affected_category=["crypto_security", "peripheral"],
        trigger_signals=["lock", "config", "security", "policy", "access", "key"],
        severity="P0", corner_type="security",
        verification_approach="uvm",
        root_cause="Security configuration register could be modified by software after boot.",
        fix_pattern="Add write-once lock bit; once set, register is read-only until reset.",
    ),
    ErrataRule(
        id="CWE-HW-003", source_family="MITRE CWE",
        errata_ref="CWE-1234",
        title="Improper lock protection for memory-mapped registers",
        description="Verify that locked registers cannot be modified through any access path — direct write, RMW, side-channel via DMA, or through alternative address aliases.",
        affected_category=["peripheral", "bus_interconnect"],
        trigger_signals=["lock", "register", "write", "dma", "alias", "remap"],
        severity="P0", corner_type="security",
        verification_approach="uvm",
        root_cause="Register lock was enforced on the primary bus path but not on a DMA alias.",
    ),
    ErrataRule(
        id="CWE-HW-004", source_family="MITRE CWE",
        errata_ref="CWE-1240",
        title="Insufficient protection of sensitive key material",
        description="Verify key material is never exposed on data buses, debug interfaces, or diagnostic ports under any operational sequence.",
        affected_category=["crypto_security"],
        trigger_signals=["key", "secret", "private", "debug", "data_out", "bus"],
        severity="P0", corner_type="security",
        verification_approach="formal",
        root_cause="Key register was readable via normal APB read path.",
        fix_pattern="Key registers return zero on read; key material only accessible internally.",
    ),
    ErrataRule(
        id="CWE-HW-005", source_family="MITRE CWE",
        errata_ref="CWE-1243",
        title="Untrusted firmware update without authentication",
        description="Verify that firmware update paths require cryptographic authentication before accepting new firmware images.",
        affected_category=["crypto_security", "peripheral"],
        trigger_signals=["firmware", "update", "boot", "image", "verify", "signature"],
        severity="P0", corner_type="security",
        verification_approach="uvm",
        root_cause="Firmware update interface accepted unsigned images.",
    ),
    ErrataRule(
        id="CWE-HW-006", source_family="MITRE CWE",
        errata_ref="CWE-1244",
        title="Insufficient granularity of address-based access control",
        description="If the access control (firewall, MPU) has granularity larger than the protected resource, adjacent resources may be unintentionally accessible. Test boundary addresses.",
        affected_category=["bus_interconnect", "processor_core"],
        trigger_signals=["firewall", "mpu", "region", "granularity", "boundary", "access"],
        severity="P0", corner_type="boundary",
        verification_approach="uvm",
        root_cause="MPU region granularity was 1KB but the sensitive peripheral occupied only 256 bytes.",
    ),
    ErrataRule(
        id="CWE-HW-007", source_family="MITRE CWE",
        errata_ref="CWE-1245",
        title="Unvalidated state machine transition",
        description="FSM accepts an invalid input that causes a transition to an undefined or unreachable state. Verify all FSM inputs are validated and illegal transitions are rejected.",
        affected_category=["universal"],
        trigger_signals=["state", "fsm", "next_state", "transition", "invalid"],
        severity="P0", corner_type="error",
        verification_approach="formal",
        root_cause="FSM case statement had no default clause — glitch on state register reached undefined state.",
        fix_pattern="Add default clause that transitions to IDLE/ERROR state; use one-hot encoding with parity.",
    ),
    ErrataRule(
        id="CWE-HW-008", source_family="MITRE CWE",
        errata_ref="CWE-1256",
        title="Hardware features enabling fault injection",
        description="Verify that voltage/clock glitch detection is present and that fault injection does not bypass security-critical decisions.",
        affected_category=["crypto_security", "clock_reset_pmu"],
        trigger_signals=["glitch", "fault", "detect", "alert", "tamper", "voltage"],
        severity="P0", corner_type="security",
        verification_approach="uvm",
        root_cause="Single comparison determined security decision — one glitch bypassed the check.",
        fix_pattern="Double-check (redundant comparison); add glitch detector on clock and power rails.",
    ),
    ErrataRule(
        id="CWE-HW-009", source_family="MITRE CWE",
        errata_ref="CWE-1260",
        title="Overlapping address ranges in protection unit",
        description="When two MPU/firewall regions overlap, verify the priority resolution is correct and the more restrictive policy wins.",
        affected_category=["bus_interconnect", "processor_core"],
        trigger_signals=["mpu", "region", "overlap", "priority", "firewall"],
        severity="P0", corner_type="boundary",
        verification_approach="uvm",
        root_cause="Overlapping regions defaulted to the first match instead of the most restrictive.",
    ),
    ErrataRule(
        id="CWE-HW-010", source_family="MITRE CWE",
        errata_ref="CWE-1262",
        title="Register interface allows write to read-only register",
        description="Verify that writing to a register documented as read-only has no effect on the register value or any side effects.",
        affected_category=["peripheral"],
        trigger_signals=["register", "read_only", "ro", "status", "write"],
        severity="P1", corner_type="protocol",
        verification_approach="unitsim",
        root_cause="Register decode logic did not mask write-enable for read-only addresses.",
    ),
    ErrataRule(
        id="CWE-HW-011", source_family="MITRE CWE",
        errata_ref="CWE-1271",
        title="Uninitialized value on reset used for security decision",
        description="After reset, a security-critical register may contain a random value that is used before software initialises it. Verify all security-critical logic has safe reset values.",
        affected_category=["crypto_security", "peripheral"],
        trigger_signals=["reset", "security", "policy", "config", "init", "default"],
        severity="P0", corner_type="reset",
        verification_approach="formal",
        root_cause="Security policy register reset value was permissive (all-access) instead of restrictive.",
        fix_pattern="Reset security registers to most-restrictive (deny-all) policy.",
    ),
    ErrataRule(
        id="CWE-HW-012", source_family="MITRE CWE",
        errata_ref="CWE-1274",
        title="Insufficient protection against side-channel attack",
        description="Verify that cryptographic operations have constant-time execution regardless of key or data values (no timing, power, or EM leakage).",
        affected_category=["crypto_security"],
        trigger_signals=["cipher", "aes", "rsa", "key", "round", "constant_time"],
        severity="P1", corner_type="security",
        verification_approach="uvm",
        root_cause="Conditional branch based on key bit value caused timing variation.",
    ),
    ErrataRule(
        id="CWE-HW-013", source_family="MITRE CWE",
        errata_ref="CWE-1276",
        title="Hot-pluggable device access before authentication",
        description="A hot-plugged device may access system resources before its identity and trust level are established. Verify access is blocked until authentication completes.",
        affected_category=["bus_interconnect", "peripheral"],
        trigger_signals=["hotplug", "plug", "detect", "auth", "trust", "access"],
        severity="P0", corner_type="security",
        verification_approach="uvm",
        root_cause="Enumeration logic granted bus master access before security handshake completed.",
    ),
    ErrataRule(
        id="CWE-HW-014", source_family="MITRE CWE",
        errata_ref="CWE-1281",
        title="Sequence of processor instructions leads to unexpected state",
        description="Specific instruction sequences (especially involving CSRs, privileged ops, and memory ordering) may lead to undefined processor state. Fuzz with random instruction sequences.",
        affected_category=["processor_core"],
        trigger_signals=["instruction", "sequence", "csr", "fence", "privilege", "state"],
        severity="P0", corner_type="hazard",
        verification_approach="uvm",
        root_cause="Instruction decoder assumed certain instruction sequences would never occur in practice.",
    ),
    ErrataRule(
        id="CWE-HW-015", source_family="MITRE CWE",
        errata_ref="CWE-1304",
        title="Persistent secret not cleared on state transition",
        description="Secrets (keys, passwords, nonces) must be zeroized when transitioning to a lower security state or on reset. Verify no secret residue remains.",
        affected_category=["crypto_security"],
        trigger_signals=["key", "secret", "zero", "clear", "wipe", "transition", "state"],
        severity="P0", corner_type="security",
        verification_approach="formal",
        root_cause="Key register was not zeroed on lifecycle transition from secure to non-secure state.",
        fix_pattern="Zeroize all secret material on any downward security state transition.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Intel / x86 Errata Patterns
# ═══════════════════════════════════════════════════════════════════════

_INTEL_ERRATA: List[ErrataRule] = [
    ErrataRule(
        id="ERRATA-INTEL-001", source_family="Intel",
        errata_ref="Intel Spec Update SKL001 (Skylake)",
        title="Store buffer forwarding with mismatched size",
        description="A narrow load that partially overlaps with a wider store in the store buffer may get incorrect forwarding. Test loads of all sizes overlapping with larger stores.",
        affected_category=["processor_core"],
        trigger_signals=["store", "load", "forward", "buffer", "size", "byte"],
        severity="P0", corner_type="hazard",
        verification_approach="uvm",
        root_cause="Store-to-load forwarding logic handled size mismatch incorrectly for byte/half-word loads.",
    ),
    ErrataRule(
        id="ERRATA-INTEL-002", source_family="Intel",
        errata_ref="Intel MFSA-2018-01 (Spectre/Meltdown class)",
        title="Speculative execution reads privileged memory",
        description="Speculative execution may access memory that the architectural privilege level should not permit. Verify no speculative data from privileged addresses is forwarded to unprivileged computations.",
        affected_category=["processor_core"],
        trigger_signals=["speculative", "privilege", "mmu", "page", "access", "cache"],
        severity="P0", corner_type="security",
        verification_approach="formal",
        root_cause="Speculative loads bypassed permission checks — data was architecturally invisible but microarchitecturally observable via cache timing.",
    ),
    ErrataRule(
        id="ERRATA-INTEL-003", source_family="Intel",
        errata_ref="Intel Spec Update BDW050",
        title="REP MOV instruction with zero count",
        description="REP-prefixed instruction with initial count of zero should be a no-op. Verify no side effects (memory writes, flag changes) occur.",
        affected_category=["processor_core"],
        trigger_signals=["rep", "count", "zero", "loop", "string"],
        severity="P1", corner_type="boundary",
        verification_approach="uvm",
        root_cause="REP implementation decremented count before checking for zero, executing one iteration.",
    ),
    ErrataRule(
        id="ERRATA-INTEL-004", source_family="Intel",
        errata_ref="Intel Spec Update HSW136",
        title="Interrupt during locked RMW operation",
        description="An interrupt arriving during a locked read-modify-write (LOCK CMPXCHG, LOCK ADD) must not break atomicity. Verify the locked operation completes before the interrupt is taken.",
        affected_category=["processor_core"],
        trigger_signals=["lock", "cmpxchg", "atomic", "rmw", "interrupt", "bus_lock"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="Interrupt priority logic could preempt the locked operation between read and write phases.",
    ),
    ErrataRule(
        id="ERRATA-INTEL-005", source_family="Intel",
        errata_ref="Intel Spec Update Generic",
        title="Self-modifying code with stale icache",
        description="After writing to instruction memory, the icache may contain stale instructions. Verify that a serializing instruction (fence.i, CPUID) properly invalidates stale entries.",
        affected_category=["processor_core"],
        trigger_signals=["icache", "self_modify", "fence", "serialize", "invalidate"],
        severity="P1", corner_type="ordering",
        verification_approach="uvm",
        root_cause="Instruction cache snoop did not cover all ways/sets for self-modifying code scenario.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Interconnect / SoC-level Errata Patterns
# ═══════════════════════════════════════════════════════════════════════

_SOC_ERRATA: List[ErrataRule] = [
    ErrataRule(
        id="ERRATA-SOC-001", source_family="SoC Integration",
        errata_ref="Industry pattern: AXI deadlock",
        title="AXI read-write ordering deadlock",
        description="In systems with shared resources, a read transaction waiting for a write response while the write channel is blocked by the read can cause deadlock. Verify no circular dependency exists.",
        affected_category=["bus_interconnect", "protocol_bridge"],
        trigger_signals=["awvalid", "arvalid", "wvalid", "bvalid", "rvalid", "ready"],
        severity="P0", corner_type="deadlock",
        verification_approach="formal",
        root_cause="Read and write channels shared a common response buffer — full buffer blocked both channels.",
    ),
    ErrataRule(
        id="ERRATA-SOC-002", source_family="SoC Integration",
        errata_ref="Industry pattern: clock domain crossing",
        title="CDC: multi-bit control signal without synchronization",
        description="A multi-bit control signal crossing clock domains without proper encoding (Gray code or handshake) may be sampled in an inconsistent state.",
        affected_category=["universal"],
        trigger_signals=["sync", "cdc", "cross", "domain", "multi_bit"],
        severity="P0", corner_type="cdc",
        verification_approach="formal",
        root_cause="Designer used two flip-flop synchronizer on a multi-bit bus instead of a handshake or Gray code.",
    ),
    ErrataRule(
        id="ERRATA-SOC-003", source_family="SoC Integration",
        errata_ref="Industry pattern: power domain",
        title="Power domain crossing without isolation cell",
        description="A signal crossing from a power-gated domain to an always-on domain without an isolation cell drives undefined (X) values. Verify isolation cells on all power domain crossings.",
        affected_category=["clock_reset_pmu"],
        trigger_signals=["isolation", "power", "domain", "crossing", "clamp", "x_prop"],
        severity="P0", corner_type="power",
        verification_approach="formal",
        root_cause="RTL did not have isolation cells — caught only by power-aware simulation.",
    ),
    ErrataRule(
        id="ERRATA-SOC-004", source_family="SoC Integration",
        errata_ref="Industry pattern: DMA vs CPU coherency",
        title="Cache coherency violation between DMA and CPU",
        description="DMA writes to memory that is cached by the CPU. The CPU may read stale data from its cache. Verify cache maintenance (flush/invalidate) is performed around DMA transfers.",
        affected_category=["dma_data_mover", "processor_core"],
        trigger_signals=["dma", "cache", "coherent", "flush", "invalidate", "snoop"],
        severity="P0", corner_type="ordering",
        verification_approach="uvm",
        root_cause="DMA wrote to DRAM but CPU L1 cache held stale copy — no snoop mechanism.",
    ),
    ErrataRule(
        id="ERRATA-SOC-005", source_family="SoC Integration",
        errata_ref="Industry pattern: interrupt controller",
        title="Interrupt priority inversion in nested interrupt scenario",
        description="A low-priority interrupt handler is running when a medium and high-priority interrupt fire simultaneously. Verify the high-priority interrupt is taken, not the medium.",
        affected_category=["peripheral", "processor_core"],
        trigger_signals=["interrupt", "priority", "nested", "pending", "active", "preempt"],
        severity="P0", corner_type="concurrent",
        verification_approach="uvm",
        root_cause="Interrupt controller priority encoder had a timing-dependent evaluation order.",
    ),
    ErrataRule(
        id="ERRATA-SOC-006", source_family="SoC Integration",
        errata_ref="Industry pattern: reset tree",
        title="Reset deassertion order violation",
        description="In a multi-domain SoC, if domain B depends on domain A (e.g. B's clocks come from A's PLL), A's reset must deassert before B's. Verify reset ordering.",
        affected_category=["clock_reset_pmu"],
        trigger_signals=["reset", "rst", "order", "sequence", "domain", "pll"],
        severity="P0", corner_type="timing",
        verification_approach="formal",
        root_cause="Reset controller released all domains simultaneously — downstream domain started before upstream PLL locked.",
    ),
    ErrataRule(
        id="ERRATA-SOC-007", source_family="SoC Integration",
        errata_ref="Industry pattern: address decode",
        title="Address decode gap between slaves",
        description="A gap in the address map between two slaves means certain addresses are not decoded by any slave. Verify the default slave responds for unmapped addresses.",
        affected_category=["bus_interconnect"],
        trigger_signals=["decode", "address", "gap", "default", "slave", "map"],
        severity="P0", corner_type="error",
        verification_approach="uvm",
        root_cause="Address map had a 4-byte gap between two peripherals — access hung forever.",
    ),
    ErrataRule(
        id="ERRATA-SOC-008", source_family="SoC Integration",
        errata_ref="Industry pattern: watchdog",
        title="Watchdog timer software disable in production",
        description="Verify the watchdog timer cannot be fully disabled by software in production mode. It should only be serviceable (kicked), not stoppable.",
        affected_category=["peripheral"],
        trigger_signals=["watchdog", "wdog", "disable", "stop", "kick", "service"],
        severity="P0", corner_type="security",
        verification_approach="uvm",
        root_cause="Watchdog had a software-writable disable bit that was not locked in production.",
    ),
    ErrataRule(
        id="ERRATA-SOC-009", source_family="SoC Integration",
        errata_ref="Industry pattern: boot ROM",
        title="Boot ROM execute-only violation",
        description="Boot ROM should be execute-only (no data reads allowed) to prevent key extraction. Verify data load instructions from boot ROM address range are rejected.",
        affected_category=["crypto_security", "memory_controller"],
        trigger_signals=["boot", "rom", "execute", "read", "load", "xonly"],
        severity="P0", corner_type="security",
        verification_approach="uvm",
        root_cause="MPU/PMP was not configured to prevent data reads from boot ROM region.",
    ),
    ErrataRule(
        id="ERRATA-SOC-010", source_family="SoC Integration",
        errata_ref="Industry pattern: clock frequency monitor",
        title="Clock frequency out of range not detected",
        description="Verify that a clock monitoring unit detects when a clock frequency drifts outside its expected range and raises an alert.",
        affected_category=["clock_reset_pmu"],
        trigger_signals=["clk_monitor", "freq", "range", "alert", "detect", "osc"],
        severity="P1", corner_type="timing",
        verification_approach="uvm",
        root_cause="Clock monitor threshold was set too wide — 2x frequency deviation was not flagged.",
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Aggregate + Lookup
# ═══════════════════════════════════════════════════════════════════════

ERRATA_RULES: List[ErrataRule] = (
    _ARM_ERRATA
    + _RISCV_ERRATA
    + _OPENTITAN_ERRATA
    + _INTEL_ERRATA
    + _SOC_ERRATA
    + _CWE_HARDWARE
)

_SOURCE_INDEX: Dict[str, List[ErrataRule]] = {}


def _ensure_errata_index() -> Dict[str, List[ErrataRule]]:
    if not _SOURCE_INDEX:
        for r in ERRATA_RULES:
            key = r.source_family.lower()
            _SOURCE_INDEX.setdefault(key, []).append(r)
    return _SOURCE_INDEX


def get_all_errata() -> List[ErrataRule]:
    """Return all errata rules."""
    return list(ERRATA_RULES)


def get_errata_by_source(source: str) -> List[ErrataRule]:
    """Return errata rules from a specific source family (case-insensitive)."""
    idx = _ensure_errata_index()
    return list(idx.get(source.lower(), []))


def get_errata_for_category(category: str) -> List[ErrataRule]:
    """Return all errata rules applicable to a design category."""
    cat_lower = category.lower()
    return [r for r in ERRATA_RULES if cat_lower in [c.lower() for c in r.affected_category]]


def match_errata_by_signals(signal_names: List[str]) -> List[ErrataRule]:
    """Return errata rules whose trigger signals match any of the given signal names."""
    lower_signals = {s.lower() for s in signal_names}
    results = []
    for r in ERRATA_RULES:
        for pattern in r.trigger_signals:
            if any(pattern.lower() in sig for sig in lower_signals):
                results.append(r)
                break
    return results
