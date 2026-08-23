//
// Copyright (c) 2024 - Present. All rights reserved.
//
// This source code is licensed under the MIT license found in the
// LICENSE file in the root directory of this source tree.
//

//------------------------------------------------------------------------------
// Module: sync_up_down_counter
//
// Description:
//   A parameterizable N-bit synchronous binary up/down counter with an
//   asynchronous active-low reset. The counter increments or decrements on the
//   positive edge of the clock when enabled. An overflow flag is asserted for
//   one cycle when the counter wraps around.
//
// Parameters:
//   WIDTH - Specifies the bit-width of the counter.
//
// Ports:
//   i_clk      - Clock signal.
//   i_arst_n   - Asynchronous reset, active-low. Resets counter to 0.
//   i_enable   - Counter enable. Counting occurs on posedge i_clk when high.
//   i_up_down  - Direction control. 1 = Count Up, 0 = Count Down.
//   o_count    - Current counter value.
//   o_overflow - Asserted for one clock cycle when the counter wraps around.
//
// Complexity: SMALL
//------------------------------------------------------------------------------

module sync_up_down_counter #(
    parameter int WIDTH = 8
) (
    input  logic             i_clk,
    input  logic             i_arst_n,
    input  logic             i_enable,
    input  logic             i_up_down,
    output logic [WIDTH-1:0] o_count,
    output logic             o_overflow
);

    //--------------------------------------------------------------------------
    // Internal signals
    //--------------------------------------------------------------------------
    logic [WIDTH-1:0] count_reg;
    logic [WIDTH-1:0] count_next;

    //--------------------------------------------------------------------------
    // Sequential logic for the counter register
    //--------------------------------------------------------------------------
    always_ff @(posedge i_clk or negedge i_arst_n) begin
        if (!i_arst_n) begin
            count_reg <= '0;
        end else begin
            count_reg <= count_next;
        end
    end

    //--------------------------------------------------------------------------
    // Combinational next-state logic for the counter
    //--------------------------------------------------------------------------
    always_comb begin
        // Default assignment: hold the current value if not enabled
        count_next = count_reg;

        if (i_enable) begin
            if (i_up_down) begin // Count up
                count_next = count_reg + 1'b1;
            end else begin // Count down
                count_next = count_reg - 1'b1;
            end
        end
    end

    //--------------------------------------------------------------------------
    // Output assignments
    //--------------------------------------------------------------------------
    assign o_count = count_reg;

    //--------------------------------------------------------------------------
    // Combinational logic for overflow detection
    //--------------------------------------------------------------------------
    logic is_max_count;
    logic is_min_count;

    assign is_max_count = (count_reg == {WIDTH{1'b1}});
    assign is_min_count = (count_reg == '0);

    assign o_overflow = (i_enable && i_up_down && is_max_count) ||
                        (i_enable && !i_up_down && is_min_count);

endmodule