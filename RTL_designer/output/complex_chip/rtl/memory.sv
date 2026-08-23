// Memory Module (Simplified)
module memory #(
    parameter DATA_WIDTH = 32,
    parameter ADDR_WIDTH = 32,
    parameter MEM_SIZE = 2048
) (
    input clk,
    input rst,
    input [ADDR_WIDTH-1:0] addr,
    output reg [DATA_WIDTH-1:0] rdata,
    input [DATA_WIDTH-1:0] wdata,
    input we
);

    localparam MEM_DEPTH = MEM_SIZE / (DATA_WIDTH/8);
    reg [DATA_WIDTH-1:0] mem [0:MEM_DEPTH-1];

    initial begin
        $readmemh("program.hex", mem); // Load program from file
    end

    always @(posedge clk) begin
        if (rst) begin
            for (int i = 0; i < MEM_DEPTH; i++) begin
                mem[i] <= 0;
            end
            rdata <= 0;
        end else begin
            if (we) begin
                mem[addr[ADDR_WIDTH-1:2]] <= wdata;
            end
            rdata <= mem[addr[ADDR_WIDTH-1:2]];
        end
    end

endmodule