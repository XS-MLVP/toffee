package AggregateTypes;
    typedef struct packed {
        logic signed [0:4] tag;
        bit valid;
    } header_t;
    typedef struct {
        header_t header;
        logic [64:0] payload [3:2];
    } entry_t;
    typedef struct {
        bit enabled;
        entry_t entries [-1:0];
    } record_t;
endpackage

module AggregateSignals (
    input logic clk,
    input logic valid,
    output logic ready,
    input AggregateTypes::header_t packed_in,
    output AggregateTypes::header_t packed_out,
    input logic [1:0][-2:1] matrix_in,
    output logic [1:0][-2:1] matrix_out,
    input var AggregateTypes::record_t record_in,
    output var AggregateTypes::record_t record_out
);
    assign ready = 1;
    assign packed_out = packed_in;
    assign matrix_out = matrix_in;
    assign record_out = record_in;
endmodule
