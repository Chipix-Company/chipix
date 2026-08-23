module up_down_counter #(
    parameter int WIDTH = 4
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        en,
    input  logic        up_down,
    output logic [WIDTH-1:0] count
);

    // Internal register for the count value
    logic [WIDTH-1:0] count_r;

    // Assign the internal register to the output port
    assign count = count_r;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            // Synchronous active-low reset: set count to 0
            count_r <= {WIDTH{1'b0}};
        end else if (en) begin
            // Counter is enabled
            if (up_down) begin
                // Up-counting: increment count
                // Wrap-around from (2^WIDTH - 1) to 0 is handled naturally by fixed-width arithmetic
                count_r <= count_r + 1'b1;
            end else begin
                // Down-counting: decrement count
                // Wrap-around from 0 to (2^WIDTH - 1) is handled naturally by fixed-width arithmetic
                count_r <= count_r - 1'b1;
            end
        end
        // If 'en' is low, count_r holds its current value (implicit in this structure)
    end

endmodule