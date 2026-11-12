"""Compare bounded array snapshots with ordinary tuple snapshots, without RTL."""

import argparse
import ctypes
import json
import statistics
import time

import xspcomm
from toffee import Bundle, SignalArray


def measure(sample, iterations, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        for _ in range(iterations):
            sample()
        samples.append(time.perf_counter_ns() - start)
    return {"median_ns_per_snapshot": statistics.median(samples) / iterations,
            "samples_ns": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elements", type=int, default=256)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if min(args.elements, args.iterations, args.repeats) <= 0:
        parser.error("elements, iterations and repeats must be positive")
    if not hasattr(xspcomm, "XDataArray"):
        parser.error("requires xcomm XDataArray and matching ABI-4 Python bindings")
    memory = (ctypes.c_uint32 * (args.elements * 3))()
    source = xspcomm.XDataArray(65, [args.elements + 1], [2])
    source.BindNativeData(ctypes.addressof(memory), [12])
    view = SignalArray(source)
    first = view[args.elements + 1]
    first.AsImmWrite()
    first.Set((1 << 64) | 7)
    bounded = Bundle(words=view)
    positional = Bundle(words=tuple(view))
    assert bounded.sample().words[args.elements + 1].as_int() == (1 << 64) | 7
    assert bounded.sample().words.as_tuple() == positional.sample().words
    cases = {"bounded": bounded.sample, "bounded_compiled": bounded.compile_sampler(),
             "tuple": positional.sample, "tuple_compiled": positional.compile_sampler()}
    print(json.dumps({"elements": args.elements, "width": 65,
                      "abi": xspcomm.abi_version(),
                      "cases": {name: measure(sample, args.iterations, args.repeats)
                                for name, sample in cases.items()}}, indent=2))


if __name__ == "__main__":
    main()
