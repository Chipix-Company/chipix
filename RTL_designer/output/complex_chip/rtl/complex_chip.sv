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