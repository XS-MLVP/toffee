module UnpackedSignals (
    input logic clk,
    input logic valid,
    output logic ready,
    input logic [64:0] matrix [3:2][-1:1],
    output logic [64:0] result [2:3][1:-1],
    input logic [7:0] cube [3:2][-1:0][4:6],
    output logic [7:0] tensor [2:3][0:-1][6:4],
    output logic [64:0] probe
);
    logic [64:0] memory [3:2][-1:1] /*verilator public_flat_rw*/;
    initial begin
        foreach (memory[i, j]) memory[i][j] = 65'(i * 10 + j);
    end
    assign ready = 1'b1;
    assign probe = memory[2][-1] ^ memory[3][1];
    for (genvar i = 2; i <= 3; ++i) begin
        for (genvar j = -1; j <= 1; ++j) begin
            assign result[i][j] = matrix[i][j] ^ 65'(i * 16 + j + 1);
        end
        for (genvar j = -1; j <= 0; ++j) begin
            for (genvar k = 4; k <= 6; ++k) begin
                assign tensor[i][j][k] = cube[i][j][k] ^ 8'(i * 16 + j * 4 + k);
            end
        end
    end
endmodule
