"""Minimal synchronous control for toffee-vs-DUT protocol isolation."""

from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path

try:
    from CacheSignalCFG import DUTCacheSignalCFG
except ImportError:
    from __init__ import DUTCacheSignalCFG

from cache_functional_toffee import (
    CMD_READ,
    CMD_READ_LAST,
    get,
    initialize_inputs,
    set_,
)


def half_cycle(clock, rising: bool) -> None:
    clock.StepHalf()
    phase = int(clock.GetPhase())
    expected = 1 if rising else 0
    assert phase == expected, f"expected phase {expected}, got {phase}"


def main(artifacts: Path) -> None:
    artifacts = artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    dut = DUTCacheSignalCFG(coverage_filename=str(artifacts / "verilator-coverage.dat"))
    dut.InitClock("clock")
    initialize_inputs(dut)
    clock = dut.GetXClock()

    try:
        set_(dut.reset, 1)
        for _ in range(2):
            half_cycle(clock, False)
            half_cycle(clock, True)
        set_(dut.reset, 0)
        for _ in range(140):
            half_cycle(clock, False)
            half_cycle(clock, True)
        assert get(dut.io_in_req_ready) == 1

        # One and only one upstream MMIO read request.
        set_(dut.io_in_req_bits_addr, 0x3000_0040)
        set_(dut.io_in_req_bits_size, 3)
        set_(dut.io_in_req_bits_cmd, CMD_READ)
        set_(dut.io_in_req_bits_wmask, 0xFF)
        set_(dut.io_in_req_bits_user, 0x55AA)
        half_cycle(clock, False)
        assert get(dut.io_in_req_ready) == 1
        set_(dut.io_in_req_valid, 1)
        half_cycle(clock, True)
        set_(dut.io_in_req_valid, 0)

        mmio_handshakes = []
        upstream_responses = []
        respond_after = -1
        response_active = False

        for _ in range(40):
            half_cycle(clock, False)
            tick = int(clock.GetHalfTick())

            if get(dut.io_mmio_req_valid) and get(dut.io_mmio_req_ready):
                mmio_handshakes.append(
                    (
                        tick,
                        get(dut.io_mmio_req_bits_addr),
                        get(dut.io_mmio_req_bits_cmd),
                    )
                )
                if respond_after < 0 and not response_active:
                    respond_after = 1

            if get(dut.io_in_resp_valid) and get(dut.io_in_resp_ready):
                upstream_responses.append(
                    (
                        tick,
                        get(dut.io_in_resp_bits_cmd),
                        get(dut.io_in_resp_bits_rdata),
                        get(dut.io_in_resp_bits_user),
                    )
                )

            if respond_after == 0:
                set_(dut.io_mmio_resp_bits_cmd, CMD_READ_LAST)
                set_(dut.io_mmio_resp_bits_rdata, 0xCAFE_BABE_8765_4321)
                set_(dut.io_mmio_resp_valid, 1)
                response_active = True
                respond_after = -1
            elif respond_after > 0:
                respond_after -= 1

            half_cycle(clock, True)
            if response_active:
                set_(dut.io_mmio_resp_valid, 0)
                response_active = False

        print(
            "direct StepHalf MMIO probe:",
            {
                "mmio_handshakes": mmio_handshakes,
                "upstream_responses": upstream_responses,
            },
        )
        assert len(mmio_handshakes) == 1, (
            "one upstream MMIO request must produce one downstream handshake"
        )
        assert len(upstream_responses) == 1

        # Repeat the async verifier's critical overlap without asyncio or
        # Toffee: return the first refill beat, then accept another read while
        # the remaining seven beats are still in flight.
        first_addr = 0x0028_04E8
        second_addr = 0x0028_04C8
        set_(dut.io_in_req_bits_addr, first_addr)
        set_(dut.io_in_req_bits_size, 3)
        set_(dut.io_in_req_bits_cmd, CMD_READ)
        set_(dut.io_in_req_bits_wmask, 0xFF)
        set_(dut.io_in_req_bits_user, 0x101)
        half_cycle(clock, False)
        set_(dut.io_in_req_valid, 1)
        clock.RefreshComb()
        assert get(dut.io_in_req_ready) == 1
        half_cycle(clock, True)
        set_(dut.io_in_req_valid, 0)

        refill = deque()
        refill_cooldown = 0
        refill_active = False
        overlap_delay = -1
        overlap_active = False
        overlap_accepted_tick = -1
        read_responses = []

        for _ in range(120):
            half_cycle(clock, False)
            tick = int(clock.GetHalfTick())
            new_refill = False

            if get(dut.io_out_mem_req_valid) and get(dut.io_out_mem_req_ready):
                assert get(dut.io_out_mem_req_bits_cmd) == 2
                request_addr = get(dut.io_out_mem_req_bits_addr)
                line = request_addr & ~0x3F
                first = (request_addr >> 3) & 0x7
                for beat in range(8):
                    index = (first + beat) & 0x7
                    cmd = CMD_READ_LAST if beat == 7 else 4
                    refill.append((cmd, 0xB000_0000_0000_0000 | (line + index * 8)))
                new_refill = True

            if overlap_delay == 0:
                set_(dut.io_in_req_bits_addr, second_addr)
                set_(dut.io_in_req_bits_user, 0x102)
                set_(dut.io_in_req_valid, 1)
                overlap_active = True
                overlap_delay = -1

            if not new_refill and refill and refill_cooldown == 0:
                cmd, data = refill[0]
                set_(dut.io_out_mem_resp_bits_cmd, cmd)
                set_(dut.io_out_mem_resp_bits_rdata, data)
                set_(dut.io_out_mem_resp_valid, 1)
                refill_active = True
            elif refill_cooldown:
                refill_cooldown -= 1

            # Refill writes can withdraw Stage1 SRAM readiness in this same
            # phase. Count acceptance only after every input drive settles.
            clock.RefreshComb()
            overlap_handshake = (overlap_active and get(dut.io_in_req_valid)
                                 and get(dut.io_in_req_ready))
            if overlap_handshake:
                overlap_accepted_tick = tick + 1
            if get(dut.io_in_resp_valid) and get(dut.io_in_resp_ready):
                response = (tick + 1, get(dut.io_in_resp_bits_cmd),
                            get(dut.io_in_resp_bits_rdata), get(dut.io_in_resp_bits_user))
                read_responses.append(response)
                if response[3] == 0x101 and overlap_delay < 0:
                    overlap_delay = 1

            half_cycle(clock, True)

            if overlap_handshake:
                set_(dut.io_in_req_valid, 0)
                overlap_active = False
            if refill_active:
                refill.popleft()
                set_(dut.io_out_mem_resp_valid, 0)
                refill_active = False
                refill_cooldown = 1
            if overlap_delay > 0:
                overlap_delay -= 1

        print(
            "direct StepHalf overlap probe:",
            {
                "accepted_tick": overlap_accepted_tick,
                "responses": read_responses,
            },
        )
        (artifacts / "direct-protocol-probe.json").write_text(json.dumps({
            "sampling": "all input drives settled before the rising acceptance edge",
            "mmio_handshakes": mmio_handshakes, "mmio_responses": upstream_responses,
            "overlap_accepted_tick": overlap_accepted_tick, "overlap_responses": read_responses,
        }, indent=2) + "\n")
        assert overlap_accepted_tick >= 0
        assert any(response[3] == 0x102 for response in read_responses), (
            "accepted overlap request produced no response"
        )
    finally:
        dut.Finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("output/cache-probe"))
    main(parser.parse_args().artifacts)
