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