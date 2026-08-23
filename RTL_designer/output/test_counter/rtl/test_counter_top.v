//------------------------------------------------------------------------------
// Module: test_counter
//
// Description:
//   A parameterizable, synchronous up-counter with synchronous active-low
//   reset and load capability.
//
// Parameters:
//   WIDTH     - Bit width of the counter.
//   INCREMENT - The value to increment the counter by on each enabled clock
//               cycle.
//
//------------------------------------------------------------------------------
module test_counter #(
    parameter int WIDTH     = 32,
    parameter int INCREMENT = 1
) (
    input  logic             clk_i,       // Clock
    input  logic             rst_n_i,     // Synchronous active-low reset
    input  logic             en_i,        // Enable for counting
    input  logic             load_i,      // Load new value
    input  logic [WIDTH-1:0] load_val_i,  // Value to load
    output logic [WIDTH-1:0] count_o,     // Current counter value
    output logic             rollover_o   // High for one cycle before rollover
);

    //--------------------------------------------------------------------------
    // Internal state register
    //--------------------------------------------------------------------------
    logic [WIDTH-1:0] count_q;

    //--------------------------------------------------------------------------
    // Sequential Logic: Counter Register
    //
    // Implements the core counter functionality with the following priority:
    // 1. Synchronous Reset
    // 2. Load
    // 3. Increment
    // 4. Hold
    //--------------------------------------------------------------------------
    always_ff @(posedge clk_i) begin
        if (!rst_n_i) begin
            count_q <= {WIDTH{1'b0}};
        end else begin
            if (load_i) begin
                count_q <= load_val_i;
            end else if (en_i) begin
                count_q <= count_q + INCREMENT;
            end
            // else: No change, counter holds its value
        end
    end

    //--------------------------------------------------------------------------
    // Combinational Output Logic
    //--------------------------------------------------------------------------

    // Assign the counter's state to the output port
    assign count_o = count_q;

    // Rollover detection:
    // Asserted for one cycle when the counter is enabled to increment and its
    // current value is such that the next increment will cause it to wrap
    // around. The load signal must be inactive for an increment to occur.
    // The condition `count_q > (MAX_VALUE - INCREMENT)` detects if adding
    // INCREMENT will exceed the maximum value.
    assign rollover_o = en_i && !load_i && (count_q > ({WIDTH{1'b1}} - INCREMENT));

endmodule