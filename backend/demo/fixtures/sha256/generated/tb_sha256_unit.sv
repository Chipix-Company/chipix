`timescale 1ns/1ps
//============================================================================
// tb_sha256_unit.sv — directed unit testbench for sha256_accelerator
// Generated during Chipix demo walkthrough (scripted LLM createFile)
//============================================================================

module tb_sha256_unit;
  logic         clk;
  logic         rst_n;
  logic         msg_valid;
  logic [511:0] msg_data;
  logic         msg_last;
  logic         msg_ready;
  logic         hash_valid;
  logic [255:0] hash_data;
  logic         hash_ready;

  // NIST SHA-256("") digest
  localparam [255:0] DIGEST_EMPTY =
    256'hE3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855;

  sha256_accelerator dut (
    .clk(clk),
    .rst_n(rst_n),
    .msg_valid(msg_valid),
    .msg_data(msg_data),
    .msg_last(msg_last),
    .msg_ready(msg_ready),
    .hash_valid(hash_valid),
    .hash_data(hash_data),
    .hash_ready(hash_ready)
  );

  initial clk = 0;
  always #5 clk = ~clk;

  integer errors;

  task automatic drive_block(input [511:0] block, input bit last_b);
    begin
      @(posedge clk);
      msg_data  <= block;
      msg_last  <= last_b;
      msg_valid <= 1'b1;
      do @(posedge clk); while (!msg_ready);
      msg_valid <= 1'b0;
      msg_last  <= 1'b0;
    end
  endtask

  initial begin
    errors = 0;
    rst_n = 0;
    msg_valid = 0;
    msg_data = '0;
    msg_last = 0;
    hash_ready = 1;
    repeat (8) @(posedge clk);
    rst_n = 1;
    repeat (2) @(posedge clk);

    // Empty-message padded block (length=0 encoding in last 64 bits)
    drive_block({1'b1, 447'b0, 64'd0}, 1'b1);

    wait (hash_valid);
    @(posedge clk);
    if (hash_data !== DIGEST_EMPTY) begin
      $error("EMPTY digest mismatch: got %h", hash_data);
      errors = errors + 1;
    end else begin
      $display("PASS: empty-message digest matches NIST vector");
    end

    if (errors == 0) $display("UNITSIM PASS — sha256_accelerator");
    else $display("UNITSIM FAIL — %0d errors", errors);
    $finish;
  end
endmodule
