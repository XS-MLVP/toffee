package T;
    typedef struct packed {
        logic [3:0] a;
        logic b;
    } s_t;
    typedef s_t [2:0] sarr_t;
endpackage
module M (
    input T::sarr_t arr,
    output T::sarr_t arr_out,
    input T::s_t [2:0] arr2,
    output T::s_t [2:0] arr2_out
);
    assign arr_out = arr;
    assign arr2_out = arr2;
endmodule
