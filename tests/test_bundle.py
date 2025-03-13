import toffee
from toffee import *


class FakeXData: ...


class FakePin:
    def __init__(self):
        self.xdata, self.event, self.value, self.mIOType = FakeXData(), None, None, 0


class FakeDUT:
    def __init__(self):
        self.io_a = FakePin()
        self.io_b = FakePin()
        self.io_c_1 = FakePin()
        self.io_c_2 = FakePin()
        self.io_d_1 = FakePin()
        self.io_d_2 = FakePin()

    def StepRis(*args, **kargs): ...


def test_bundle():
    toffee.setup_logging(log_level=toffee.logger.INFO)
    dut = FakeDUT()

    class BundleB(Bundle):
        signals = ["1", "2", "3"]

    class BundleA(Bundle):
        signals = ["a", "b", "e"]

        def __init__(self):
            super().__init__()
            self.c = BundleB.from_prefix(prefix="c_")
            self.d = BundleB.from_prefix(prefix="d_")

    bundle_1 = BundleA().set_name("bundle_1").bind(dut)
    bundle_1.set_prefix("io_")
    bundle_1.bind(dut)

    bundle_2 = BundleA.from_regex(regex="io_(.*)").bind(dut)

    print(bundle_2)

    bundle_3 = (
        BundleA.from_dict(
            {
                "a": "io_a",
                "b": "io_b",
                "c_1": "io_c_1",
                "c_2": "io_c_2",
                "d_1": "io_d_1",
                "abcdefg": "io_abcdefg",
            }
        )
        .set_name("bundle_3")
        .bind(dut)
    )

    bundle_1.assign({"a": 1, "b": 2, "c.1": 3, "c.2": 4}, multilevel=False)
    print(bundle_1.as_dict(multilevel=False))

    bundle_1.assign({"a": 5, "b": 6, "c": {"1": 7, "2": 8}}, multilevel=True)
    print(bundle_1.as_dict(multilevel=True))

    class BundleC(Bundle):
        signals = ["a", "b"]

        def __init__(self):
            super().__init__()
            self.c = Bundle.new_class_from_list(["1", "2", "3"]).from_prefix("c_")
            self.d = Bundle.new_class_from_list(["1", "2", "3"]).from_prefix("d_")

    bundle_4 = BundleC.from_prefix("io_").set_name("bundle_4").bind(dut)

    for signal in bundle_4.all_signals():
        print(signal)

    # bundle_4.set_all(666)
    print(bundle_4.as_dict())

    bundle_4.assign(
        {
            # "*": 777,
            "a": 1,
            "c": {
                "1": 3,
                # "*": 888,
            },
        },
        multilevel=True,
    )
    print(bundle_4.as_dict())

    class BundleD(Bundle):
        signals = ["a", "b"]

        def __init__(self):
            super().__init__()
            self.c = Bundle.new_class_from_list(["1", "2", "3", "4"]).from_dict(
                {"1": "c_1", "2": "c_2", "4": "c_4"}
            )

    bundle_5 = BundleD.from_prefix("io_").set_name("bundle_5").bind(dut)

    print(bundle_5.all_signals_rule())

    bundle_5.assign(
        {
            # "*": 999,
            "9": 1,
            "c.1": 4,
            "c.5.43": 3,
            "q": 4,
            "c": {
                "1": 3,
                "66": 4,
                # "*": 888,
            },
        },
        multilevel=True,
    )

    bundle_5.assign(
        {
            # "*": 999,
            "9": 1,
            "c.1": 4,
            "c.5.43": 3,
            "q": 4,
            "c": {
                "1": 3,
            },
        },
        multilevel=False,
    )


# Signal List Test


def test_signal_list():
    toffee.setup_logging(INFO)

    class MyDUT(FakeDUT):
        def __init__(self):
            self.io_a, self.io_b = FakePin(), FakePin()
            self.io_vec_0, self.io_vec_1, self.io_vec_2 = (
                FakePin(),
                FakePin(),
                FakePin(),
            )

    class BundleWithSignalList(Bundle):
        a, b = Signals(2)
        vec = SignalList("vec_#", 3)

    bundle = BundleWithSignalList.from_prefix("io_").set_name("bundle")
    bundle.bind(MyDUT())

    bundle.as_dict(multilevel=True)
    bundle.as_dict(multilevel=False)

    bundle.assign({"a": 1, "b": 2, "vec": [3, 4, 5]}, multilevel=True)
    bundle.assign({"a": 1, "b": 2, "vec": [3, 4, 5]}, multilevel=False)


def test_bundle_list():
    toffee.setup_logging(INFO)

    class MyDUT(FakeDUT):
        def __init__(self):
            self.io_a, self.io_b = FakePin(), FakePin()
            self.io_vec_0_a, self.io_vec_0_b, self.io_vec_1_a, self.io_vec_1_b = (
                FakePin(),
                FakePin(),
                FakePin(),
                FakePin(),
            )

    class SubBundle(Bundle):
        a, b = Signals(2)

    class BundleWithBundleList(Bundle):
        c, d = Signals(2)
        vec = BundleList(SubBundle, "vec_#_", 2)

    bundle = BundleWithBundleList.from_prefix("io_").set_name("bundle")
    bundle.bind(MyDUT())

    print(bundle)
    print(list(bundle.all_signals()))
    print(bundle.as_dict(multilevel=True))
    print(bundle.as_dict(multilevel=False))
    print(bundle.all_signals_rule())

    bundle.assign(
        {"c": 1, "d": 2, "vec": [{"a": 3, "b": 4}, {"a": 5, "b": 6}]}, multilevel=True
    )
    bundle.assign(
        {"c": 1, "d": 2, "vec": [{"a": 3, "b": 4}, {"a": 5, "b": 6}]}, multilevel=False
    )
