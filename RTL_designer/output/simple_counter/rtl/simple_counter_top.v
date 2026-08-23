// Copyright 2023 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * @file simple_counter.sv
 * @brief A simple, parameterizable, synchronous N-bit binary up-counter.
 */
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