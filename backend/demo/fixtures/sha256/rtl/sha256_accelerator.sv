module sha256_accelerator (
  input wire        clk,
  input wire        rst_n,

  // Message input interface
  input wire        msg_valid,
  input wire [511:0] msg_data,
  input wire        msg_last,
  output wire       msg_ready,

  // Hash output interface
  output wire       hash_valid,
  output wire [255:0] hash_data,
  input wire        hash_ready
);

  // SHA-256 Constants (K)
  // These are the first 32 bits of the fractional parts of the cube roots of the first 64 prime numbers.
  localparam [31:0] K [0:63] = {
    32'h428a2f98, 32'h71374491, 32'hb5c0fbcf, 32'he9b5dba5, 32'h3956c25b, 32'h59f111f1, 32'h923f82a4, 32'hab1c5ed5,
    32'hd807aa98, 32'h12835b01, 32'h243185be, 32'h550c7dc3, 32'h72be5d74, 32'h80deb1fe, 32'h9bdc06a7, 32'hc19bf174,
    32'he49b69c1, 32'hefbe4786, 32'h0fc19dc6, 32'h240ca1cc, 32'h2de92c6f, 32'h4a7484aa, 32'h5cb0a9dc, 32'h76f988da,
    32'h983e5152, 32'ha831c66d, 32'hb00327c8, 32'hbf597fc7, 32'hc6e00bf3, 32'hd5a79147, 32'h06ca6351, 32'h14292967,
    32'h27b70a85, 32'h2e1b2138, 32'h4d2c6dfc, 32'h53380d13, 32'h650a7354, 32'h766a0abb, 32'h81c2c92e, 32'h92722c85,
    32'ha2bfe8a1, 32'ha81a664b, 32'hc24b8b70, 32'hc76c51a3, 32'hd192e819, 32'hd6990624, 32'hf40e3585, 32'h106aa070,
    32'h19a4c116, 32'h1e376c08, 32'h2748774c, 32'h34b0bcb5, 32'h391c0cb3, 32'h4ed8aa4a, 32'h5b9cca4f, 32'h682e6ff3,
    32'h748f82ee, 32'h78a5636f, 32'h84c87814, 32'h8cc70208, 32'h90befffa, 32'ha4506ceb, 32'hbef9a3f7, 32'hc67178f2
  };

  // Initial Hash Values (H0)
  // These are the first 32 bits of the fractional parts of the square roots of the first 8 prime numbers.
  localparam [31:0] H_INIT [0:7] = {
    32'h6a09e667, 32'hbb67ae85, 32'h3c6ef372, 32'ha54ff53a,
    32'h510e527f, 32'h9b05688c, 32'h1f83d9ab, 32'h5be0cd19
  };

  // State machine for overall control
  typedef enum logic [2:0] {
    STATE_IDLE,         // Waiting for new message
    STATE_PAD_GENERATE, // Generate the padded block
    STATE_PROCESS_BLOCK, // Processing a 512-bit block
    STATE_OUTPUT_HASH   // Outputting the final hash
  } sha_state_e;

  sha_state_e current_state, next_state;

  // Internal registers for hash values
  logic [31:0] h_reg [0:7];

  // Working variables for block processing
  logic [31:0] a, b, c, d, e, f, g, h;
  logic [31:0] a_next, b_next, c_next, d_next, e_next, f_next, g_next, h_next_var;

  // Message schedule array W
  logic [31:0] W [0:63];

  // Message block buffer
  logic [511:0] msg_block_buffer;
  logic [63:0] total_msg_len_bits; // Total message length in bits

  // Padding logic registers
  logic        padding_block_generated; // Flag to indicate padding block has been generated and is ready for processing
  logic        padding_block_processed; // Flag to indicate padding block has finished processing

  // Block processing iteration counter
  logic [5:0] t_counter; // 0 to 63 for 64 rounds

  // Output hash registers
  logic [255:0] final_hash_data;
  logic         final_hash_valid;

  // Control signals
  logic         load_initial_h;
  logic         load_h_from_block_result;
  logic         start_block_processing;
  logic         block_processing_done; // From the 64-round pipeline
  logic         output_hash_enable;
  logic         clear_total_msg_len; // Also clears padding flags

  // FSM for overall control
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      current_state <= STATE_IDLE;
      total_msg_len_bits <= 0;
      padding_block_generated <= 1'b0;
      padding_block_processed <= 1'b0;
    end else begin
      current_state <= next_state;
      if (clear_total_msg_len) begin
        total_msg_len_bits <= 0;
        padding_block_generated <= 1'b0;
        padding_block_processed <= 1'b0;
      end else if (current_state == STATE_PROCESS_BLOCK && msg_valid && msg_ready) begin
        total_msg_len_bits <= total_msg_len_bits + 512;
      end
      // Update padding flags
      if (current_state == STATE_PAD_GENERATE && next_state == STATE_PROCESS_BLOCK) begin
        padding_block_generated <= 1'b1;
      end
      if (current_state == STATE_PROCESS_BLOCK && block_processing_done && padding_block_generated) begin
        padding_block_processed <= 1'b1;
      end
    end
  end

  // Next state logic
  always_comb begin
    next_state = current_state;
    load_initial_h = 1'b0;
    load_h_from_block_result = 1'b0;
    start_block_processing = 1'b0;
    output_hash_enable = 1'b0;
    clear_total_msg_len = 1'b0;
    msg_ready = 1'b0;
    final_hash_valid = 1'b0;

    case (current_state)
      STATE_IDLE: begin
        clear_total_msg_len = 1'b1; // Reset all state for new hash
        load_initial_h = 1'b1;
        msg_ready = 1'b1; // Ready to accept first message block
        if (msg_valid) begin
          next_state = STATE_PROCESS_BLOCK;
          start_block_processing = 1'b1;
        end
      end

      STATE_PROCESS_BLOCK: begin
        // Ready for next message block only if current block is done and it's not the last message block
        msg_ready = block_processing_done && !msg_last;
        if (block_processing_done) begin
          load_h_from_block_result = 1'b1;
          if (padding_block_processed) begin // If we just finished processing the padded block
            next_state = STATE_OUTPUT_HASH;
            output_hash_enable = 1'b1;
          end else if (msg_last) begin // Last message block processed, need to pad
            next_state = STATE_PAD_GENERATE;
          end else if (msg_valid) begin // More message blocks to process
            next_state = STATE_PROCESS_BLOCK;
            start_block_processing = 1'b1;
          end else begin // Waiting for next message block
            next_state = STATE_PROCESS_BLOCK; // Stay, msg_ready will be low
          end
        end
      end

      STATE_PAD_GENERATE: begin
        // This state is for generating the padding block. It's a single-cycle state.
        next_state = STATE_PROCESS_BLOCK;
        start_block_processing = 1'b1; // Immediately start processing the generated padding block
      end

      STATE_OUTPUT_HASH: begin
        final_hash_valid = 1'b1;
        if (hash_ready) begin
          next_state = STATE_IDLE;
        end
      end
    endcase
  end

  // Registers for current hash values (H)
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i = 0; i < 8; i++) h_reg[i] <= H_INIT[i];
    end else begin
      if (load_initial_h) begin
        for (int i = 0; i < 8; i++) h_reg[i] <= H_INIT[i];
      end else if (load_h_from_block_result) begin
        h_reg[0] <= h_reg[0] + a_next;
        h_reg[1] <= h_reg[1] + b_next;
        h_reg[2] <= h_reg[2] + c_next;
        h_reg[3] <= h_reg[3] + d_next;
        h_reg[4] <= h_reg[4] + e_next;
        h_reg[5] <= h_reg[5] + f_next;
        h_reg[6] <= h_reg[6] + g_next;
        h_reg[7] <= h_reg[7] + h_next_var;
      end
    end
  end

  // Message block buffer loading and padding block generation
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      msg_block_buffer <= 512'b0;
    end else begin
      if (current_state == STATE_PROCESS_BLOCK && msg_valid && msg_ready) begin
        msg_block_buffer <= msg_data;
      end else if (current_state == STATE_PAD_GENERATE) begin
        // Correct padding: 1 bit, 447 zeros, 64-bit message length
        // This forms a 512-bit block: 32'h80000000 (1 followed by 31 zeros)
        // followed by 13 words (416 bits) of zeros,
        // followed by the 64-bit total message length.
        msg_block_buffer <= {32'h80000000, 13 * 32'h00000000, total_msg_len_bits[63:32], total_msg_len_bits[31:0]};
      end
    end
  end

  // Message schedule (W) generation
  // W[t] for t = 0 to 15 are derived from the current 512-bit message block.
  // W[t] for t = 16 to 63 are derived from previous W values.
  always_comb begin
    for (int i = 0; i < 16; i++) begin
      // Big-endian conversion: msg_block_buffer[511:0] is 16 words.
      // msg_block_buffer[511:480] is W[0]
      // msg_block_buffer[479:448] is W[1]
      // ...
      // msg_block_buffer[31:0] is W[15]
      W[i] = msg_block_buffer[511 - (i * 32) -: 32];
    end
  end

  // Helper function for Rotate Right (ROTR)
  function automatic [31:0] ROTR(input [31:0] x, input int n);
    ROTR = (x >>> n) | (x << (32 - n));
  endfunction

  // W[t] generation for t = 16 to 63 (combinational logic)
  // Sigma0(x) = ROTR(x, 7) XOR ROTR(x, 18) XOR SHR(x, 3)
  // Sigma1(x) = ROTR(x, 17) XOR ROTR(x, 19) XOR SHR(x, 10)
  function automatic [31:0] sigma0(input [31:0] x);
    sigma0 = ROTR(x, 7) ^ ROTR(x, 18) ^ (x >> 3);
  endfunction

  function automatic [31:0] sigma1(input [31:0] x);
    sigma1 = ROTR(x, 17) ^ ROTR(x, 19) ^ (x >> 10);
  endfunction

  genvar i;
  generate
    for (i = 16; i < 64; i++) begin : gen_W_calc
      always_comb begin
        W[i] = sigma1(W[i-2]) + W[i-7] + sigma0(W[i-15]) + W[i-16];
      end
    end
  endgenerate

  // Block processing (64 rounds)
  // This will be a sequential block processing 64 rounds.
  // We need a counter for the rounds (t_counter).
  // The working variables a,b,c,d,e,f,g,h are updated each round.

  // Working variables registers
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      a <= 0; b <= 0; c <= 0; d <= 0;
      e <= 0; f <= 0; g <= 0; h <= 0;
      t_counter <= 0;
      block_processing_done <= 1'b0;
    end else begin
      block_processing_done <= 1'b0; // Default to not done

      if (start_block_processing) begin
        a <= h_reg[0]; b <= h_reg[1]; c <= h_reg[2]; d <= h_reg[3];
        e <= h_reg[4]; f <= h_reg[5]; g <= h_reg[6]; h <= h_reg[7];
        t_counter <= 0;
      end else if (current_state == STATE_PROCESS_BLOCK) begin // Only advance rounds if in PROCESS_BLOCK
        if (t_counter < 63) begin
          a <= a_next; b <= b_next; c <= c_next; d <= d_next;
          e <= e_next; f <= f_next; g <= g_next; h <= h_next_var;
          t_counter <= t_counter + 1;
        end else begin // t_counter == 63, last round
          a <= a_next; b <= b_next; c <= c_next; d <= d_next;
          e <= e_next; f <= f_next; g <= g_next; h <= h_next_var;
          t_counter <= 0; // Reset for next block
          block_processing_done <= 1'b1;
        end
      end
    end
  end

  // Combinational logic for one SHA-256 round
  // Ch(x, y, z) = (x AND y) XOR (NOT x AND z)
  // Maj(x, y, z) = (x AND y) XOR (x AND z) XOR (y AND z)
  // Sum0(x) = ROTR(x, 2) XOR ROTR(x, 13) XOR ROTR(x, 22)
  // Sum1(x) = ROTR(x, 6) XOR ROTR(x, 11) XOR ROTR(x, 25)

  function automatic [31:0] Ch(input [31:0] x, input [31:0] y, input [31:0] z);
    Ch = (x & y) ^ (~x & z);
  endfunction

  function automatic [31:0] Maj(input [31:0] x, input [31:0] y, input [31:0] z);
    Maj = (x & y) ^ (x & z) ^ (y & z);
  endfunction

  function automatic [31:0] Sum0(input [31:0] x);
    Sum0 = ROTR(x, 2) ^ ROTR(x, 13) ^ ROTR(x, 22);
  endfunction

  function automatic [31:0] Sum1(input [31:0] x);
    Sum1 = ROTR(x, 6) ^ ROTR(x, 11) ^ ROTR(x, 25);
  endfunction

  // Round computations
  logic [31:0] T1, T2;

  always_comb begin
    T1 = h + Sum1(e) + Ch(e, f, g) + K[t_counter] + W[t_counter];
    T2 = Sum0(a) + Maj(a, b, c);

    a_next = T1 + T2;
    b_next = a;
    c_next = b;
    d_next = c;
    e_next = d + T1;
    f_next = e;
    g_next = f;
    h_next_var = g;
  end

  // Output hash
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      final_hash_data <= 0;
    end else begin
      if (output_hash_enable) begin
        final_hash_data <= {h_reg[0], h_reg[1], h_reg[2], h_reg[3],
                            h_reg[4], h_reg[5], h_reg[6], h_reg[7]};
      end
    end
  end

  assign hash_valid = final_hash_valid;
  assign hash_data = final_hash_data;

endmodule