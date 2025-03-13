import pdb


def info(*args, **kwargs):
    print("***", end=" ")
    print(*args, **kwargs)


def debug(*args, **kwargs):
    print("Debug:", end=" ")
    print(*args, **kwargs)


def error(*args, **kwargs):
    print("Error:", end=" ")
    print(*args, **kwargs)


class PdbToffee(pdb.Pdb):
    def __init__(self, dut, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dut = dut
        self.prompt = "toffee> "

    def do_tstep(self, arg):
        if not arg:
            arg = 1
        else:
            try:
                arg = int(arg)
            except Exception as e:
                info("parse cycles from '%s' fail: " % arg, e)
                return
        self.dut.Step(arg)
        self.dut.FlushWaveform()

    def do_tflush(self, arg):
        if arg:
            info("flush need no args")
            return
        self.dut.FlushWaveform()

    def do_tlist(self, arg):
        prefix = ""
        deep = 99
        if arg:
            args = arg.split()
            deep = args[0]
            if len(args) > 1:
                prefix = args[1]
            try:
                deep = int(deep)
            except Exception as e:
                info("parse deep from '%s' fail: " % deep, e)
                return
        for s in self.dut.VPIInternalSignalList(prefix, deep):
            info(s)

    def do_tprint(self, arg):
        if not arg:
            return
        arg = arg.split()
        name = arg[0]
        if len(arg) > 1:
            stype = arg[1]
            stype = stype.lower()
            if stype not in ["bin", "hex", "dec"]:
                error("invalid print type '%s'" % stype)
                return
        else:
            stype = "dec"
        signal = self.dut.GetInternalSignal(name)
        if signal is None:
            error("signal '%s' not found" % name)
            return
        if stype == "bin":
            info("(width: %d) value:" % signal.W(), bin(signal.value))
        elif stype == "hex":
            info("(width: %d) value:" % signal.W(), hex(signal.value))
        else:
            info("(width: %d) value:" % signal.W(), signal.value)

    def do_tset(self, arg):
        if not arg:
            return
        arg = arg.split()
        name = arg[0]
        if len(arg) < 2:
            error("need value to set, usage: tset signal_name value")
            return
        value = arg[1]
        try:
            value = int(value)
        except Exception as e:
            error("parse value from '%s' fail: " % value, e)
            return
        signal = self.dut.GetInternalSignal(name)
        if signal is None:
            error("signal '%s' not found" % name)
            return
        signal.value = value
        self.dut.RefreshComb()

    def do_tforce(self, arg):
        if not arg:
            return
        arg = arg.split()
        name = arg[0]
        if len(arg) < 2:
            error("need value to force, usage: force signal_name value")
            return
        value = arg[1]
        try:
            value = int(value)
        except Exception as e:
            error("parse value from '%s' fail: " % value, e)
            return
        signal = self.dut.GetInternalSignal(name)
        if signal is None:
            error("signal '%s' not found" % name)
            return
        signal.SetIgnoreSameDataWrite(False)
        signal.AsVPIWriteForce()
        signal.value = value
        self.dut.RefreshComb()
        signal.SetIgnoreSameDataWrite(True)

    def do_trelease(self, arg):
        if not arg:
            return
        signal = self.dut.GetInternalSignal(arg)
        if signal is None:
            error("signal '%s' not found" % arg)
            return
        signal.SetIgnoreSameDataWrite(False)
        signal.AsVPIWriteRelease()
        signal.value = signal.value
        self.dut.RefreshComb()
        signal.AsVPIWriteNoDelay()
        signal.SetIgnoreSameDataWrite(True)


def get(dut, *args, **kwargs):
    return PdbToffee(dut, *args, **kwargs)
