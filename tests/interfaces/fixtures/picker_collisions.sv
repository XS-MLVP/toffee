module Collide (
    input logic data,
    input logic [3:0] data_0,
    input logic req,
    input logic req_valid,
    output logic data_out,
    output logic [3:0] data_0_out,
    output logic req_out,
    output logic req_valid_out
);
    assign data_out = data;
    assign data_0_out = data_0;
    assign req_out = req;
    assign req_valid_out = req_valid;
endmodule
