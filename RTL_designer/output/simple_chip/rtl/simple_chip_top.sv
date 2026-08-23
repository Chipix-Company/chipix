module simple_chip #(
    parameter integer COUNTER_WIDTH = 8,
    parameter integer RESET_VALUE = 0
) (
    input  logic clk,
    input  logic rst,
    input  logic enable,
    output logic [COUNTER_WIDTH-1:0] count
);

    reg [COUNTER_WIDTH-1:0] count_reg;

    always_ff @(posedge clk) begin
        if (rst) begin
            count_reg <= RESET_VALUE;
        } else if (enable) begin
            count_reg <= count_reg + 1;
        }
    end

    assign count = count_reg;

endmodule