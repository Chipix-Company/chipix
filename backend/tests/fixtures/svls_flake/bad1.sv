module A;
reg a;
always @* begin
    a = b | c;
end
endmodule
