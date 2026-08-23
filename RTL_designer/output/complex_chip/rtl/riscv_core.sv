// RISC-V Core (Simplified)
module riscv_core #(
    parameter DATA_WIDTH = 32,
    parameter ADDR_WIDTH = 32
) (
    input clk,
    input rst,
    output reg [ADDR_WIDTH-1:0] mem_addr,
    input [DATA_WIDTH-1:0] mem_rdata,
    output reg [DATA_WIDTH-1:0] mem_wdata,
    output reg mem_we
);

    reg [ADDR_WIDTH-1:0] pc;
    reg [DATA_WIDTH-1:0] instruction;
    reg [DATA_WIDTH-1:0] regfile [0:31]; // Simplified register file

    // Instruction Decode
    wire [6:0] opcode;
    wire [4:0] rd;
    wire [2:0] funct3;
    wire [4:0] rs1;
    wire [4:0] rs2;
    wire [6:0] funct7;
    wire [31:0] imm;

    assign opcode = instruction[6:0];
    assign rd     = instruction[11:7];
    assign funct3 = instruction[15:13];
    assign rs1    = instruction[19:15];
    assign rs2    = instruction[24:20];
    assign funct7 = instruction[31:25];
    assign imm    = instruction[31:20]; // Immediate (example)

    // ALU Output
    reg [DATA_WIDTH-1:0] alu_out;

    always @(posedge clk) begin
        if (rst) begin
            pc <= 0;
            mem_addr <= 0;
            mem_wdata <= 0;
            mem_we <= 0;
            for (int i = 0; i < 32; i++) begin
                regfile[i] <= 0;
            end
        end else begin
            // Fetch
            mem_addr <= pc;
            instruction <= mem_rdata;

            // Decode and Execute (Simplified)
            case (opcode)
                7'b0110011: begin // R-type (ADD)
                    if (funct3 == 3'b000 && funct7 == 7'b0000000) begin
                        alu_out <= regfile[rs1] + regfile[rs2];
                        regfile[rd] <= alu_out;
                        mem_we <= 0;
                    end else begin
                        mem_we <= 0;
                    end
                end
                7'b0010011: begin // I-type (ADDI)
                    if (funct3 == 3'b000) begin
                        alu_out <= regfile[rs1] + imm;
                        regfile[rd] <= alu_out;
                        mem_we <= 0;
                    end else begin
                        mem_we <= 0;
                    end
                end
                7'b0000011: begin // Load Word (LW)
                    if (funct3 == 3'b010) begin
                        mem_addr <= regfile[rs1] + imm;
                        regfile[rd] <= mem_rdata;
                        mem_we <= 0;
                    end else begin
                        mem_we <= 0;
                    end
                end
                7'b0100011: begin // Store Word (SW)
                    if (funct3 == 3'b010) begin
                        mem_addr <= regfile[rs1] + imm;
                        mem_wdata <= regfile[rs2];
                        mem_we <= 1;
                    end else begin
                        mem_we <= 0;
                    end
                end
                default: begin
                    mem_we <= 0;
                end
            endcase

            pc <= pc + 4; // Increment PC
        end
    end

endmodule