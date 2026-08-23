module complex_chip #(
    parameter DATA_WIDTH = 32,
    parameter ADDR_WIDTH = 32,
    parameter MEM_SIZE = 2048,
    parameter UART_BAUD_RATE = 115200,
    parameter CLOCK_FREQUENCY = 50000000
) (
    input clk,
    input rst,
    output uart_tx,
    input uart_rx
);

    // Memory Address Range
    localparam MEM_START_ADDR = 32'h00000000;
    localparam MEM_END_ADDR   = 32'h000007FF;

    // UART Address Range
    localparam UART_BASE_ADDR = 32'hFFFFFFF0;
    localparam UART_TX_ADDR   = 32'hFFFFFFF0;
    localparam UART_RX_ADDR   = 32'hFFFFFFF4;
    localparam UART_STATUS_ADDR = 32'hFFFFFFF8;
    localparam UART_CTRL_ADDR   = 32'hxFFFFFFFC;

    // Internal Signals
    wire [ADDR_WIDTH-1:0]  cpu_addr;
    wire [DATA_WIDTH-1:0]  cpu_rdata;
    wire [DATA_WIDTH-1:0]  cpu_wdata;
    wire                  cpu_we;

    // RISC-V Core
    riscv_core #(
        .DATA_WIDTH(DATA_WIDTH),
        .ADDR_WIDTH(ADDR_WIDTH)
    ) u_riscv_core (
        .clk(clk),
        .rst(rst),
        .mem_addr(cpu_addr),
        .mem_rdata(cpu_rdata),
        .mem_wdata(cpu_wdata),
        .mem_we(cpu_we)
    );

    // Memory Module
    memory #(
        .DATA_WIDTH(DATA_WIDTH),
        .ADDR_WIDTH(ADDR_WIDTH),
        .MEM_SIZE(MEM_SIZE)
    ) u_memory (
        .clk(clk),
        .rst(rst),
        .addr(cpu_addr),
        .rdata(cpu_rdata),
        .wdata(cpu_wdata),
        .we(cpu_we)
    );

    // UART Module
    uart #(
        .DATA_WIDTH(8),
        .BAUD_RATE(UART_BAUD_RATE),
        .CLOCK_FREQUENCY(CLOCK_FREQUENCY)
    ) u_uart (
        .clk(clk),
        .rst(rst),
        .rx(uart_rx),
        .tx(uart_tx),
        .mem_addr(cpu_addr),
        .mem_rdata(cpu_rdata),
        .mem_wdata(cpu_wdata),
        .mem_we(cpu_we)
    );

endmodule

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

// UART Module (Simplified)
module uart #(
    parameter DATA_WIDTH = 8,
    parameter BAUD_RATE = 115200,
    parameter CLOCK_FREQUENCY = 50000000
) (
    input clk,
    input rst,
    input rx,
    output reg tx,
    input [31:0] mem_addr,
    output reg [31:0] mem_rdata,
    input [31:0] mem_wdata,
    input mem_we
);

    localparam integer CLOCKS_PER_BIT = CLOCK_FREQUENCY / BAUD_RATE;

    // TX Logic
    reg [DATA_WIDTH-1:0] tx_data;
    reg tx_enable;
    reg [31:0] tx_bit_counter;
    reg [3:0] tx_state;
    localparam IDLE = 0, START = 1, DATA = 2, STOP = 3;
    reg [DATA_WIDTH-1:0] tx_shift_reg;

    // RX Logic
    reg [DATA_WIDTH-1:0] rx_data;
    reg rx_enable;
    reg [31:0] rx_bit_counter;
    reg [3:0] rx_state;
    localparam RX_IDLE = 0, RX_START = 1, RX_DATA = 2, RX_STOP = 3;
    reg [DATA_WIDTH-1:0] rx_shift_reg;
    reg rx_valid;

    // Status and Control Registers
    reg tx_ready;
    reg rx_ready;

    always @(posedge clk) begin
        if (rst) begin
            // TX
            tx <= 1;
            tx_data <= 0;
            tx_enable <= 0;
            tx_bit_counter <= 0;
            tx_state <= IDLE;
            tx_shift_reg <= 0;

            // RX
            rx_data <= 0;
            rx_enable <= 0;
            rx_bit_counter <= 0;
            rx_state <= RX_IDLE;
            rx_shift_reg <= 0;
            rx_valid <= 0;

            // Status and Control
            tx_ready <= 1;
            rx_ready <= 0;
            mem_rdata <= 0;
        end else begin
            // TX Logic
            case (tx_state)
                IDLE: begin
                    if (tx_enable && tx_ready) begin
                        tx_state <= START;
                        tx_bit_counter <= 0;
                        tx_shift_reg <= tx_data;
                        tx_ready <= 0;
                    end else begin
                        tx <= 1;
                    end
                end
                START: begin
                    tx <= 0;
                    tx_bit_counter <= tx_bit_counter + 1;
                    if (tx_bit_counter == CLOCKS_PER_BIT) begin
                        tx_bit_counter <= 0;
                        tx_state <= DATA;
                    end
                end
                DATA: begin
                    tx <= tx_shift_reg[0];
                    tx_shift_reg <= {1'b0, tx_shift_reg[DATA_WIDTH-1:1]};
                    tx_bit_counter <= tx_bit_counter + 1;
                    if (tx_bit_counter == CLOCKS_PER_BIT) begin
                        tx_bit_counter <= 0;
                        if (tx_shift_reg == 0) begin
                            tx_state <= STOP;
                        end
                    end
                end
                STOP: begin
                    tx <= 1;
                    tx_bit_counter <= tx_bit_counter + 1;
                    if (tx_bit_counter == CLOCKS_PER_BIT) begin
                        tx_state <= IDLE;
                        tx_enable <= 0;
                        tx_ready <= 1;
                    end
                end
            endcase

            // RX Logic
            case (rx_state)
                RX_IDLE: begin
                    rx_valid <= 0;
                    if (rx == 0) begin
                        rx_state <= RX_START;
                        rx_bit_counter <= 0;
                    end
                end
                RX_START: begin
                    rx_bit_counter <= rx_bit_counter + 1;
                    if (rx_bit_counter == CLOCKS_PER_BIT/2) begin // Sample in the middle
                        if (rx == 0) begin
                            rx_state <= RX_DATA;
                            rx_bit_counter <= 0;
                            rx_shift_reg <= 0;
                        end else begin
                            rx_state <= RX_IDLE;
                        end
                    end
                end
                RX_DATA: begin
                    rx_bit_counter <= rx_bit_counter + 1;
                    if (rx_bit_counter == CLOCKS_PER_BIT) begin
                        rx_bit_counter <= 0;
                        rx_shift_reg <= {rx, rx_shift_reg[DATA_WIDTH-1:1]};
                        if (rx_shift_reg[DATA_WIDTH-1:0] != 0) begin
                            rx_state <= RX_STOP;
                        end
                    end
                end
                RX_STOP: begin
                    rx_bit_counter <= rx_bit_counter + 1;
                    if (rx_bit_counter == CLOCKS_PER_BIT) begin
                        if (rx == 1) begin
                            rx_data <= rx_shift_reg;
                            rx_valid <= 1;
                            rx_ready <= 1;
                        end
                        rx_state <= RX_IDLE;
                    end
                end
            endcase

            // Memory mapped UART access
            if (mem_we) begin
                if (mem_addr == UART_TX_ADDR && tx_ready) begin // TX
                    tx_data <= mem_wdata[7:0];
                    tx_enable <= 1; // Start transmission
                end
            end

            if (mem_addr == UART_RX_ADDR) begin // RX
                if (rx_valid) begin
                    mem_rdata <= {24'b0, rx_data};
                    rx_valid <= 0;
                    rx_ready <= 0;
                end else begin
                    mem_rdata <= 0;
                end
            end else if (mem_addr == UART_STATUS_ADDR) begin
                mem_rdata <= {24'b0, {rx_ready, tx_ready, 6'b0}}; // Example status
            end else begin
                mem_rdata <= 0;
            end
        end
    end

endmodule