// Copyright 2023 Your Company
// SPDX-License-Identifier: Apache-2.0

// Description:
// This module implements a synchronous, parameterizable N-bit binary up/down counter
// with an asynchronous active-low reset. It features an enable signal, direction
// control, and a single-cycle overflow flag to indicate when the counter wraps around.

`default_nettype none

module sync_up_down_counter #(
    parameter int WIDTH = 8
) (
    input  logic             clk,
    input  logic             arst_n,
    input  logic             en,
    input  logic             up_down,
    output logic [WIDTH-1:0] count_out,
    output logic             overflow
);

    // Internal state register
    logic [WIDTH-1:0] count_reg;

    // Next-state logic signal
    logic [WIDTH-1:0] count_next;

    // Combinational logic for the counter's next state
    always_comb begin
        // Default assignment to hold current state and avoid latches
        count_next = count_reg;

        if (en) begin
            if (up_down) begin // Counting up
                count_next = count_reg + 1'b1;
            end else begin // Counting down
                count_next = count_reg - 1'b1;
            end
        end
    end

    // Sequential logic for the counter register
    always_ff @(posedge clk or negedge arst_n) begin
        if (!arst_n) begin
            count_reg <= '0;
        end else begin
            count_reg <= count_next;
        end
    end

    // Output assignments
    assign count_out = count_reg;

    // Combinational logic for the overflow flag.
    // The flag asserts for one cycle when the counter is at a boundary
    // and is enabled to wrap around. This output is combinatorial to ensure
    // it is asserted in the same cycle as the boundary condition.
    assign overflow = en && (
                        (up_down && (count_reg == '1)) ||
                        (!up_down && (count_reg == '0))
                      );

endmodule : sync_up_down_counter

`default_nettype wire