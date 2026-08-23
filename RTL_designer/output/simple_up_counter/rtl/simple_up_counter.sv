module simple_up_counter #(
    parameter int WIDTH = 8,
    parameter logic [WIDTH-1:0] MAX_VAL = (1<<WIDTH)-1
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        en,
    output logic [WIDTH-1:0] count_out,
    output logic        max_count_reached
);

    // Internal register to hold the current count value
    logic [WIDTH-1:0] count_reg;

    // Synchronous sequential logic for the counter
    always_ff @(posedge clk) begin
        if (rst) begin
            // Synchronous active-high reset: count_reg resets to 0
            count_reg <= '0;
        end else if (en) begin
            // Counter is enabled: increment or wrap
            if (count_reg == MAX_VAL) begin
                // If current count is MAX_VAL, wrap around to 0
                count_reg <= '0;
            end else begin
                // Otherwise, increment the count
                count_reg <= count_reg + 1;
            end
        end
        // If 'en' is deasserted, 'count_reg' holds its current value
    end

    // Assign the internal register value to the output port
    assign count_out = count_reg;

    // Combinatorial logic for max_count_reached output
    // Asserted when the current count_out equals MAX_VAL
    assign max_count_reached = (count_reg == MAX_VAL);

endmodule