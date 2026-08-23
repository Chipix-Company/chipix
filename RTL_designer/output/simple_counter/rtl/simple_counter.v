module simple_counter #(
    parameter int WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst,
    input  logic             en,
    output logic [WIDTH-1:0] count_out,
    output logic             rollover
);

    logic [WIDTH-1:0] count_reg;

    // The counter register logic.
    // It resets to 0, increments when enabled, and holds otherwise.
    always_ff @(posedge clk) begin
        if (rst) begin
            count_reg <= '0;
        end else if (en) begin
            count_reg <= count_reg + 1'b1;
        end
    end

    // Assign the registered counter value to the output.
    assign count_out = count_reg;

    // The rollover signal is asserted for one cycle when the counter is at its
    // maximum value and is enabled to increment, which will cause it to wrap
    // to zero on the next clock edge.
    assign rollover = (count_reg == {WIDTH{1'b1}}) && en;

endmodule