from mem import *

from toffee.debug import get


def custom_F1(self):
    print("F1 called")


def custom_F2(self):
    print("F2 called")


def test_mem():
    dut = DUTmem("+vpi")
    dut.InitClock("clk")
    pdb = get(dut)
    # reset
    dut.rst_n.value = 0
    dut.Step(1)
    dut.rst_n.value = 1
    dut.Step(1)
    pdb.set_trace()
    for bank in range(2):
        for addr in range(16):
            dut.bank.value = bank
            dut.addr.value = addr
            dut.data_in.value = addr
            dut.write_enable.value = 1
            dut.Step(1)
    for bank in range(2):
        for addr in range(16):
            dut.bank.value = bank
            dut.addr.value = addr
            dut.write_enable.value = 0
            dut.Step(1)
            print("bank: %d, addr: %d, data: %d" % (bank, addr, dut.data_out.value))


if __name__ == "__main__":
    test_mem()
