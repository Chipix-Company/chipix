# SHA-256 Hash Accelerator — Functional Specification (Demo Fixture)
# Chipix demo only — not a production IP datasheet.

## 1. Overview

`sha256_accelerator` is a streaming SHA-256 hash engine that accepts 512-bit
message blocks over a ready/valid handshake and produces a 256-bit digest.

Target use: SoC crypto offload, secure boot measurement, and software digest
acceleration.

## 2. Interfaces

### Clock / Reset
- `clk` — rising-edge clock
- `rst_n` — asynchronous active-low reset

### Message input
- `msg_valid` — asserted when `msg_data` is valid
- `msg_data[511:0]` — 512-bit SHA-256 block
- `msg_last` — marks the final padded block of the message
- `msg_ready` — backpressure from the accelerator

### Hash output
- `hash_valid` — digest ready
- `hash_data[255:0]` — SHA-256 digest
- `hash_ready` — consumer ready

## 3. Functional requirements

1. **REQ-IDLE**: After reset the FSM shall enter `STATE_IDLE` with `msg_ready=1`
   and `hash_valid=0`.
2. **REQ-HANDSHAKE**: A block transfers when `msg_valid && msg_ready` in the same cycle.
3. **REQ-ROUNDS**: Each accepted block shall execute exactly 64 compression rounds
   using the NIST K constants and message schedule W[0:63].
4. **REQ-IV**: The first block of a message shall load H0..H7 from the SHA-256 IV.
5. **REQ-CHAIN**: Subsequent blocks shall chain from the previous intermediate hash.
6. **REQ-LAST**: When `msg_last` is accepted, after compression completes the engine
   shall present `hash_valid` with the final digest.
7. **REQ-BACKPRESSURE**: While processing a block, `msg_ready` shall deassert until
   the engine can accept another block.
8. **REQ-OUTPUT-STALL**: If `hash_valid && !hash_ready`, the digest shall hold stable.
9. **REQ-EMPTY**: Single-block empty message (padded externally) shall produce the
   known SHA-256("") digest `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

## 4. Non-goals

- On-chip padding engine is out of scope for v1 (pad in software / DMA).
- HMAC / SHA-224 modes are out of scope.

## 5. Verification intent

- Unit simulation: directed empty-message + multi-block vectors with scoreboard.
- Formal: prove handshake safety, 64-round completion, and reset clears hash_valid.
