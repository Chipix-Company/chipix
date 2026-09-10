// sha256_accelerator_formal.sv — SVA properties (demo generateFormalProperties)
`timescale 1ns/1ps

module sha256_accelerator_props (
  input logic         clk,
  input logic         rst_n,
  input logic         msg_valid,
  input logic         msg_ready,
  input logic         msg_last,
  input logic         hash_valid,
  input logic         hash_ready
);

  // After reset, hash_valid must be low
  property p_reset_clears_hash;
    @(posedge clk) !rst_n |=> !hash_valid;
  endproperty
  a_reset_clears_hash: assert property (p_reset_clears_hash);

  // Handshake: transfer only when both valid and ready
  property p_msg_transfer_stable;
    @(posedge clk) disable iff (!rst_n)
      (msg_valid && !msg_ready) |=> msg_valid;
  endproperty
  a_msg_transfer_stable: assert property (p_msg_transfer_stable);

  // Digest holds while consumer is not ready
  property p_hash_hold;
    @(posedge clk) disable iff (!rst_n)
      (hash_valid && !hash_ready) |=> hash_valid;
  endproperty
  a_hash_hold: assert property (p_hash_hold);

  cover_msg_last: cover property (@(posedge clk) msg_valid && msg_ready && msg_last);
  cover_hash_fire: cover property (@(posedge clk) hash_valid && hash_ready);

endmodule

bind sha256_accelerator sha256_accelerator_props u_props (.*);
