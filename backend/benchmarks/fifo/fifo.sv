// ═══════════════════════════════════════════════════════════════════════
// Synchronous FIFO — Benchmark Design for ChipVerify AI
// Configurable depth and width with full/empty flags
// ═══════════════════════════════════════════════════════════════════════

module fifo #(
  parameter DEPTH = 16,
  parameter WIDTH = 8
)(
  input  logic              clk,
  input  logic              rst_n,
  // Write interface
  input  logic              wr_en,
  input  logic [WIDTH-1:0]  data_in,
  // Read interface
  input  logic              rd_en,
  output logic [WIDTH-1:0]  data_out,
  // Status flags
  output logic              full,
  output logic              empty,
  output logic [$clog2(DEPTH):0] count
);

  // Internal memory
  logic [WIDTH-1:0] mem [0:DEPTH-1];

  // Pointers
  logic [$clog2(DEPTH)-1:0] wr_ptr;
  logic [$clog2(DEPTH)-1:0] rd_ptr;
  logic [$clog2(DEPTH):0]   fifo_count;

  // Status flags
  assign full  = (fifo_count == DEPTH);
  assign empty = (fifo_count == 0);
  assign count = fifo_count;

  // Write logic
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wr_ptr <= '0;
    end else if (wr_en && !full) begin
      mem[wr_ptr] <= data_in;
      wr_ptr <= wr_ptr + 1'b1;
    end
  end

  // Read logic
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_ptr   <= '0;
      data_out <= '0;
    end else if (rd_en && !empty) begin
      data_out <= mem[rd_ptr];
      rd_ptr   <= rd_ptr + 1'b1;
    end
  end

  // Count logic
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      fifo_count <= '0;
    end else begin
      case ({wr_en && !full, rd_en && !empty})
        2'b10:   fifo_count <= fifo_count + 1'b1;
        2'b01:   fifo_count <= fifo_count - 1'b1;
        default: fifo_count <= fifo_count;
      endcase
    end
  end

endmodule
