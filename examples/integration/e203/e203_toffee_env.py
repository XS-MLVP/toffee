"""Toffee verification environment for ``e203_ifu_ift2icb``.

The environment deliberately models the protocol at transaction level while
driving all pins through memory-direct XData.  Its reference model predicts
both architectural instruction data and the bridge's 0/1/2-command
optimization; it never derives expected results from DUT response outputs.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Any

from toffee import (
    Bin,
    Bundle,
    CheckContext,
    CoverageDatabase,
    CoverGroup,
    CoverGroupDef,
    CoverPointDef,
    CrossDef,
    FallingEdge,
    RisingEdge,
    Execution,
    Scoreboard,
    ScoreboardMode,
    XCommClockBackend,
    as_xdata,
    parse_lcov,
)

# Keep the shared project observer importable when this example is run directly.
_REPOSITORY = Path(__file__).resolve().parents[3]
if str(_REPOSITORY) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY))
from examples.integration.e203.e203_internal_coverage import E203InternalObserver


ITCM_BASE = 0x8000_0000
ITCM_REGION_MASK = 0xFFFF_0000
MASK32 = 0xFFFF_FFFF
MASK64 = 0xFFFF_FFFF_FFFF_FFFF
TRACE = os.environ.get("E203_TRACE") == "1"


def trace(message: str) -> None:
    if TRACE:
        print(message, flush=True)


def get(signal: Any) -> int:
    return int(signal.value)


def set_(signal: Any, value: int) -> None:
    signal.Set(int(value))


def load_generated_dut(directory: str | Path) -> type:
    """Load a generated picker package without relying on its directory name."""

    root = Path(directory).resolve()
    init = root / "__init__.py"
    if not init.is_file():
        raise FileNotFoundError(
            f"generated e203 package not found at {init}; run build_toffee.sh"
        )
    module_name = f"_picker_e203_generated_{abs(hash(str(root)))}"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(
            module_name,
            init,
            submodule_search_locations=[str(root)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load generated DUT package {root}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module.DUTe203_ifu_ift2icb


class ByteMemory:
    """Deterministic immutable byte-addressable instruction memory."""

    PATTERNS = ("address", "zero", "ones", "alternating", "walking")

    def __init__(self, pattern: str = "address") -> None:
        if pattern not in self.PATTERNS:
            raise ValueError(f"unknown memory pattern: {pattern}")
        self.pattern = pattern

    def byte(self, address: int) -> int:
        address &= MASK32
        if self.pattern == "zero":
            return 0
        if self.pattern == "ones":
            return 0xFF
        if self.pattern == "alternating":
            return 0xAA if address & 1 else 0x55
        if self.pattern == "walking":
            return 1 << (address & 7)
        mixed = (
            address * 0x9E37_79B1
            ^ (address >> 7)
            ^ (address << 11)
            ^ 0xA5C3_6D17
        ) & MASK32
        return (mixed ^ (mixed >> 8) ^ (mixed >> 16) ^ (mixed >> 24)) & 0xFF

    def read(self, address: int, size: int) -> int:
        return sum(self.byte(address + offset) << (8 * offset) for offset in range(size))

    def instruction(self, pc: int) -> int:
        return self.read(pc, 4) & MASK32

    def itcm_lane(self, address: int) -> int:
        return self.read(address & ~0x7, 8) & MASK64

    def biu_lane(self, address: int) -> int:
        return self.read(address & ~0x3, 4) & MASK32


@dataclass(frozen=True, slots=True)
class FetchRequest:
    pc: int
    sequential: bool = False
    seq_rv32: bool = False
    last_pc: int | None = None
    holdup: bool = False
    itcm_nohold: bool = False
    command_stall: int = 0
    second_command_stall: int = 0
    response_delay: int = 0
    second_response_delay: int | None = None
    response_stall: int = 0
    error_uops: frozenset[int] = frozenset()
    unselected_response: bool = False

    def __post_init__(self) -> None:
        if self.pc & 1:
            raise ValueError("e203 IFU request PC must be halfword aligned")
        for name in ("command_stall", "second_command_stall", "response_delay", "response_stall"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.second_response_delay is not None and self.second_response_delay < 0:
            raise ValueError("second_response_delay must not be negative")
        if any(index < 0 or index > 1 for index in self.error_uops):
            raise ValueError("error_uops may only contain command indexes 0 or 1")

    @property
    def effective_last_pc(self) -> int:
        return self.pc if self.last_pc is None else self.last_pc


@dataclass(frozen=True, slots=True)
class ExpectedFetch:
    target: str
    lane_bytes: int
    alignment: str
    lane_cross: bool
    lane_same: bool
    lane_holdup: bool
    command_addresses: tuple[int, ...]
    instruction: int
    error: bool


@dataclass(frozen=True, slots=True)
class IcbCommand:
    tick: int
    target: str
    address: int
    ordinal: int
    stalled: bool


@dataclass(frozen=True, slots=True)
class FetchTransaction:
    pc: int
    target: str
    alignment: str
    lane_cross: bool
    sequential: bool
    holdup: bool
    itcm_nohold: bool
    command_count: int
    command_backpressure: bool
    response_backpressure: bool
    response: str
    latency: int
    accepted_tick: int
    completed_tick: int
    instruction: int
    seq_rv32: bool
    second_command_backpressure: bool
    error_pattern: int
    route_switch: bool
    response_delay_order: str


class IfuReferenceModel:
    def __init__(self, memory: ByteMemory) -> None:
        self.memory = memory
        self.previous_cross = False

    @staticmethod
    def target(pc: int) -> str:
        return "itcm" if (pc & ITCM_REGION_MASK) == ITCM_BASE else "biu"

    def predict(self, request: FetchRequest) -> ExpectedFetch:
        target = self.target(request.pc)
        lane_bytes = 8 if target == "itcm" else 4
        offset = request.pc & (lane_bytes - 1)
        lane_cross = offset == lane_bytes - 2
        lane_begin = offset == 0
        alignment = (
            "lane_begin"
            if lane_begin
            else "lane_cross"
            if lane_cross
            else "middle"
        )
        lane_same = request.sequential and (
            self.previous_cross if lane_begin else True
        )
        lane_holdup = (
            target == "itcm" and request.holdup and not request.itcm_nohold
        )
        same_cross_holdup = lane_same and lane_cross and lane_holdup
        need_two = (
            lane_same and lane_cross and not lane_holdup
        ) or (not lane_same and lane_cross)
        need_zero = lane_same and not lane_cross and lane_holdup

        if need_zero:
            addresses: tuple[int, ...] = ()
        elif same_cross_holdup:
            offset_from_last = 6 if request.seq_rv32 else 4
            addresses = ((request.effective_last_pc + offset_from_last) & MASK32,)
        elif need_two:
            addresses = (
                request.pc & MASK32,
                # The second uop is issued after the request handshake.  At
                # that point the upstream pc_r reflected by ifu_req_last_pc
                # has advanced to this accepted PC.
                (request.pc + 2) & MASK32,
            )
        else:
            addresses = (request.pc & MASK32,)

        effective_errors = request.error_uops & frozenset(range(len(addresses)))
        error = bool(effective_errors)
        expected = ExpectedFetch(
            target,
            lane_bytes,
            alignment,
            lane_cross,
            lane_same,
            lane_holdup,
            addresses,
            self.memory.instruction(request.pc),
            error,
        )
        return expected

    def accept(self, request: FetchRequest) -> ExpectedFetch:
        expected = self.predict(request)
        self.previous_cross = expected.lane_cross
        return expected

    def reset(self) -> None:
        self.previous_cross = False


@dataclass(slots=True)
class _PendingResponse:
    available_cycle: int
    command: IcbCommand
    data: int
    error: bool


def e203_coverage_definition() -> CoverGroupDef:
    points = (
        CoverPointDef(
            "target",
            {"itcm": Bin.values("itcm"), "biu": Bin.values("biu")},
            description="Checks that fetches exercise both the local ITCM and external BIU routing paths.",
        ),
        CoverPointDef(
            "alignment",
            {
                "lane_begin": Bin.values("lane_begin"),
                "middle": Bin.values("middle"),
                "lane_cross": Bin.values("lane_cross"),
            },
            description="Classifies the fetch PC position within a memory lane, including requests that cross a lane boundary.",
        ),
        CoverPointDef(
            "sequential",
            {"no": Bin.values(False), "yes": Bin.values(True)},
            description="Distinguishes sequential instruction fetches from redirected or non-sequential fetches.",
        ),
        CoverPointDef(
            "holdup",
            {"no": Bin.values(False), "yes": Bin.values(True)},
            description="Records whether lane assembly requires the IFU to retain partial fetch data across commands.",
        ),
        CoverPointDef(
            "command_count",
            {"zero": Bin.values(0), "one": Bin.values(1), "two": Bin.values(2)},
            description="Counts the ICB commands emitted for one architectural fetch transaction.",
        ),
        CoverPointDef(
            "command_backpressure",
            {"no": Bin.values(False), "yes": Bin.values(True)},
            description="Records whether any emitted ICB command was stalled by command-channel backpressure.",
        ),
        CoverPointDef(
            "response_backpressure",
            {"no": Bin.values(False), "yes": Bin.values(True)},
            description="Records whether the IFU response was held while its consumer deasserted ready.",
        ),
        CoverPointDef(
            "response",
            {"ok": Bin.values("ok"), "error": Bin.values("error")},
            description="Checks both successful and error-returning fetch completions.",
        ),
        CoverPointDef(
            "latency",
            {
                "fast": Bin.range(1, 5),
                "medium": Bin.range(6, 12),
                "slow": Bin.range(13, 10_000),
            },
            description="Buckets end-to-end fetch latency from request start through completed IFU response.",
        ),
        CoverPointDef("seq_rv32", {"no": Bin.values(False), "yes": Bin.values(True)},
                      description="Includes sequential +4 fetches and their next-lane address calculation."),
        CoverPointDef("second_command_backpressure", {
            "no": Bin.values(False), "yes": Bin.values(True),
        }, description="Stalls the second split command after the first response, exercising WAIT2ND."),
        CoverPointDef("error_pattern", {
            "none": Bin.values(0), "first": Bin.values(1),
            "second": Bin.values(2), "both": Bin.values(3),
        }, description="Checks first/second/both response errors against the architectural error OR."),
        CoverPointDef("route_switch", {"no": Bin.values(False), "yes": Bin.values(True)},
                      description="A split fetch may cross the ITCM region boundary and use both targets."),
        CoverPointDef("response_delay_order", {
            name: Bin.values(name) for name in ("not_split", "equal", "first_slower", "second_slower")
        }, description="Checks split fetches with independent first/second ICB response delays."),
        CoverPointDef("itcm_nohold", {"no": Bin.values(False), "yes": Bin.values(True)},
                      description="Explicit nohold must disable the held-lane optimization."),
    )
    crosses = (
        CrossDef(
            "target_x_alignment",
            ("target", "alignment"),
            include=(
                ("itcm", "lane_begin"),
                ("itcm", "middle"),
                ("itcm", "lane_cross"),
                ("biu", "lane_begin"),
                ("biu", "lane_cross"),
            ),
            description="Checks meaningful combinations of memory target and fetch alignment, including boundary crossings.",
        ),
        CrossDef(
            "target_x_response",
            ("target", "response"),
            description="Checks that success and error responses are observed on both ITCM and BIU routes.",
        ),
        CrossDef(
            "holdup_x_commands",
            ("holdup", "command_count"),
            include=(
                ("no", "one"),
                ("no", "two"),
                ("yes", "zero"),
                ("yes", "one"),
            ),
            description="Checks the expected relationship between lane holdup and the number of generated ICB commands.",
        ),
        CrossDef(
            "command_bp_x_response_bp",
            ("command_backpressure", "response_backpressure"),
            description="Exercises independent and simultaneous pressure on the ICB command and IFU response sides.",
        ),
    )
    return CoverGroupDef(
        "e203_ifu_ift2icb",
        points,
        crosses,
        description=(
            "Validates end-to-end IFU fetch behavior across routing, alignment, lane "
            "assembly, backpressure, response status, and latency scenarios."
        ),
    )


class E203Harness:
    def __init__(
        self,
        dut: Any,
        *,
        seed: int = 1,
        coverage_enabled: bool = True,
        data_pattern: str = "address",
    ) -> None:
        self.dut = dut
        self.memory = ByteMemory(data_pattern)
        self.internal = E203InternalObserver(dut, self.memory, seed=seed, data_pattern=data_pattern)
        self.reference = IfuReferenceModel(self.memory)
        self.scoreboard = Scoreboard("e203.fetch")
        self.request_bits = Bundle(
            pc=dut.ifu_req_pc, sequential=dut.ifu_req_seq,
            seq_rv32=dut.ifu_req_seq_rv32, last_pc=dut.ifu_req_last_pc,
        )
        self.response_bits = Bundle(instruction=dut.ifu_rsp_instr, error=dut.ifu_rsp_err)
        self.random = random.Random(seed)
        self.coverage: CoverGroup = e203_coverage_definition().instantiate(
            "e203_ifu_ift2icb"
        )
        self.coverage_enabled = coverage_enabled
        self.commands: list[IcbCommand] = []
        self.transactions: list[FetchTransaction] = []
        self._pending: deque[_PendingResponse] = deque()
        self._active: _PendingResponse | None = None
        self._cycle = 0
        self._at_falling = False
        self._request_serial = 0
        self._request_command_ordinal = 0
        self._command_stall_left = 0
        self._second_command_stall = 0
        self._second_command_stall_applied = False
        self._held_command: tuple[str, int] | None = None
        self._response_delay = 0
        self._second_response_delay = 0
        self.active_request: dict[str, Any] | None = None
        self._error_uops: frozenset[int] = frozenset()
        self._command_was_stalled = False
        self._active_bus_stable: tuple[str, int, int, int] | None = None
        self._foreign_response = False
        self._stream_candidate: FetchRequest | None = None
        self.stream_records: list[dict[str, Any]] = []
        self.command_ready_targets: frozenset[str] = frozenset({"itcm", "biu"})

    def initialize_inputs(self) -> None:
        for signal in vars(self.dut).values():
            xdata = as_xdata(signal)
            if hasattr(xdata, "IsInIO") and xdata.IsInIO():
                xdata.AsImmWrite()
                xdata.Set(0)
        set_(self.dut.itcm_region_indic, ITCM_BASE)
        set_(self.dut.ifu_rsp_ready, 1)
        self.dut.RefreshComb()

    async def reset(self, cycles: int = 3) -> None:
        if not self._at_falling:
            await FallingEdge(self.dut.clk)
            self._at_falling = True
        set_(self.dut.rst_n, 0)
        set_(self.dut.ifu_req_valid, 0)
        set_(self.dut.ifu_rsp_ready, 1)
        self._pending.clear()
        self._active = None
        self._active_bus_stable = None
        self._held_command = None
        self._drive_bus_idle()
        for _ in range(cycles):
            await self._cycle_once()
        set_(self.dut.rst_n, 1)
        await self._cycle_once()
        self.reference.reset()
        self.active_request = None

    def idle_inputs(self) -> None:
        """Withdraw testbench drives on exit; this does not complete a pending fetch."""
        set_(self.dut.ifu_req_valid, 0)
        set_(self.dut.ifu_rsp_ready, 1)
        self._drive_bus_idle()

    def _drive_bus_idle(self) -> None:
        set_(self.dut.ifu2itcm_icb_cmd_ready, 0)
        set_(self.dut.ifu2biu_icb_cmd_ready, 0)
        set_(self.dut.ifu2itcm_icb_rsp_valid, 0)
        set_(self.dut.ifu2biu_icb_rsp_valid, 0)
        set_(self.dut.ifu2itcm_icb_rsp_err, 0)
        set_(self.dut.ifu2biu_icb_rsp_err, 0)

    def _target_command(self) -> tuple[str, int] | None:
        itcm_valid = bool(get(self.dut.ifu2itcm_icb_cmd_valid))
        biu_valid = bool(get(self.dut.ifu2biu_icb_cmd_valid))
        if itcm_valid and biu_valid:
            raise AssertionError("DUT asserted ITCM and BIU command valid together")
        if itcm_valid:
            return "itcm", get(self.dut.ifu2itcm_icb_cmd_addr)
        if biu_valid:
            return "biu", get(self.dut.ifu2biu_icb_cmd_addr)
        return None

    def _response_ready(self, target: str) -> bool:
        signal = (
            self.dut.ifu2itcm_icb_rsp_ready
            if target == "itcm"
            else self.dut.ifu2biu_icb_rsp_ready
        )
        return bool(get(signal))

    def _promote_response(self) -> None:
        if (
            self._active is None
            and self._pending
            and self._pending[0].available_cycle <= self._cycle
        ):
            self._active = self._pending.popleft()

    def _drive_response(self) -> None:
        set_(self.dut.ifu2itcm_icb_rsp_valid, 0)
        set_(self.dut.ifu2biu_icb_rsp_valid, 0)
        set_(self.dut.ifu2itcm_icb_rsp_err, 0)
        set_(self.dut.ifu2biu_icb_rsp_err, 0)
        if self._active is None:
            self._active_bus_stable = None
            return
        response = self._active
        if response.command.target == "itcm":
            set_(self.dut.ifu2itcm_icb_rsp_rdata, response.data)
            set_(self.dut.ifu2itcm_icb_rsp_err, response.error)
            set_(self.dut.ifu2itcm_icb_rsp_valid, 1)
        else:
            set_(self.dut.ifu2biu_icb_rsp_rdata, response.data)
            set_(self.dut.ifu2biu_icb_rsp_err, response.error)
            set_(self.dut.ifu2biu_icb_rsp_valid, 1)
        if self._foreign_response:
            other = "biu" if response.command.target == "itcm" else "itcm"
            set_(getattr(self.dut, f"ifu2{other}_icb_rsp_valid"), 1)
            set_(getattr(self.dut, f"ifu2{other}_icb_rsp_err"), 1)
            set_(getattr(self.dut, f"ifu2{other}_icb_rsp_rdata"), 0xdeadbeef)

    def _make_response(self, command: IcbCommand) -> _PendingResponse:
        if command.target == "itcm":
            full_address = ITCM_BASE | (command.address & 0xFFFF)
            data = self.memory.itcm_lane(full_address)
        else:
            full_address = command.address & MASK32
            data = self.memory.biu_lane(full_address)
        return _PendingResponse(
            self._cycle + (self._second_response_delay if command.ordinal == 1
                           else self._response_delay) + 1,
            command,
            data,
            command.ordinal in self._error_uops,
        )

    async def _cycle_once(self) -> dict[str, Any]:
        if not self._at_falling:
            raise RuntimeError("cycle must begin at falling-stable phase")
        self._promote_response()
        self._drive_response()

        # First propagate response valid: a first-uop response can
        # combinationally create the second command in this same cycle.
        self.dut.RefreshComb()
        command_before_ready = self._target_command()
        if (command_before_ready is not None and self._request_command_ordinal == 1
                and not self._second_command_stall_applied):
            self._command_stall_left = self._second_command_stall
            self._second_command_stall_applied = True
            self._command_was_stalled = False
        stall_command = ((command_before_ready is not None or get(self.dut.ifu_req_valid))
                         and self._command_stall_left > 0)
        ready = 0 if stall_command else 1
        set_(self.dut.ifu2itcm_icb_cmd_ready, ready and "itcm" in self.command_ready_targets)
        set_(self.dut.ifu2biu_icb_cmd_ready, ready and "biu" in self.command_ready_targets)
        self.dut.RefreshComb()

        command_view = self._target_command()
        if self._held_command is not None:
            assert command_view == self._held_command, (
                f"ICB command changed under backpressure: {self._held_command} -> {command_view}"
            )
        self._held_command = command_view if stall_command else None
        if stall_command:
            self._command_stall_left -= 1
            self._command_was_stalled = True

        request_accept = bool(get(self.dut.ifu_req_valid) and get(self.dut.ifu_req_ready))
        command_accept = (command_view is not None and bool(ready)
                          and command_view[0] in self.command_ready_targets)
        response_accept = (
            self._active is not None
            and self._response_ready(self._active.command.target)
        )
        output_valid = bool(get(self.dut.ifu_rsp_valid))
        output_accept = output_valid and bool(get(self.dut.ifu_rsp_ready))
        output_snapshot = self.response_bits.sample() if output_valid else None
        output_payload = (
            output_snapshot.instruction.as_int(), output_snapshot.error.as_int(),
        ) if output_snapshot is not None else None

        if self._active is not None:
            stable = (
                self._active.command.target,
                self._active.data,
                int(self._active.error),
                self._active.command.address,
            )
            if self._active_bus_stable is not None:
                assert stable == self._active_bus_stable, (
                    "ICB response changed under backpressure: "
                    f"{self._active_bus_stable} -> {stable}"
                )
            if not response_accept:
                self._active_bus_stable = stable

        event = await RisingEdge(self.dut.clk)
        self._at_falling = False

        accepted_command: IcbCommand | None = None
        if request_accept and self._stream_candidate is not None:
            candidate = self._stream_candidate
            self._request_command_ordinal = 0
            self._response_delay = candidate.response_delay
            self._second_response_delay = (candidate.response_delay if candidate.second_response_delay is None
                                           else candidate.second_response_delay)
            self._error_uops = candidate.error_uops
            self._foreign_response = candidate.unselected_response
            self._second_command_stall_applied = False
        if command_accept:
            assert command_view is not None
            target, address = command_view
            accepted_command = IcbCommand(
                tick=int(self.dut.GetXClock().GetHalfTick()),
                target=target,
                address=address,
                ordinal=self._request_command_ordinal,
                stalled=self._command_was_stalled,
            )
            self._request_command_ordinal += 1
            self.commands.append(accepted_command)
            self._pending.append(self._make_response(accepted_command))
            trace(f"ICB command {accepted_command}")

        if response_accept:
            self._active = None
            self._active_bus_stable = None

        await FallingEdge(self.dut.clk)
        self._at_falling = True
        self._cycle += 1
        return {
            "event": event,
            "request_accept": request_accept,
            "command": accepted_command,
            "response_accept": response_accept,
            "output_valid": output_valid,
            "output_accept": output_accept,
            "output": output_payload,
        }

    async def stream_fetches(self, requests: list[FetchRequest], *, consumer_stall: int = 0,
                             timeout_cycles: int = 500) -> list[dict[str, Any]]:
        """Offer the next request immediately, allowing response/request handoff.

        This finite stream checks every delivered instruction and its accepted
        commands against the same byte model as serial fetches. Command stalls
        belong to serial cases; consumer backpressure applies to the stream.
        """
        if consumer_stall < 0 or timeout_cycles <= 0:
            raise ValueError("invalid stream budget")
        if any(r.command_stall or r.second_command_stall or r.response_stall for r in requests):
            raise ValueError("stream requests use stream consumer_stall and ready commands")
        if not requests:
            return []
        if not self._at_falling:
            await FallingEdge(self.dut.clk)
            self._at_falling = True
        self._command_stall_left = self._second_command_stall = 0
        self._command_was_stalled = False
        accepted, completed, active = 0, 0, None
        pending = deque()
        records = []
        stable_output = None
        try:
            for cycle in range(timeout_cycles):
                if accepted < len(requests):
                    request = requests[accepted]
                    self._stream_candidate = request
                    self.request_bits.drive({"pc": request.pc, "sequential": request.sequential,
                                             "seq_rv32": request.seq_rv32,
                                             "last_pc": (active["request"].pc if active is not None
                                                         else request.effective_last_pc)})
                    set_(self.dut.ifu2itcm_holdup, request.holdup)
                    set_(self.dut.itcm_nohold, request.itcm_nohold)
                    set_(self.dut.ifu_req_valid, 1)
                else:
                    self._stream_candidate = None
                    set_(self.dut.ifu_req_valid, 0)
                set_(self.dut.ifu_rsp_ready, cycle >= consumer_stall)
                step = await self._cycle_once()
                if step["request_accept"]:
                    expected = self.reference.accept(request)
                    active = {"request": request, "expected": expected,
                              "accepted_event": step["event"], "commands": []}
                    pending.append(active)
                    accepted += 1
                    set_(self.dut.ifu_req_last_pc, request.pc)
                if step["command"] is not None:
                    assert active is not None, "command before accepted stream request"
                    active["commands"].append(step["command"])
                if stable_output is not None:
                    assert step["output_valid"] and step["output"] == stable_output, (
                        "stream response withdrawn or changed under backpressure")
                stable_output = step["output"] if step["output_valid"] and not step["output_accept"] else None
                if step["output_accept"]:
                    assert pending, "stream output without accepted request"
                    item = pending.popleft()
                    expected = item["expected"]
                    expected_commands = tuple((IfuReferenceModel.target(addr),
                                               addr & (0xffff if IfuReferenceModel.target(addr) == "itcm" else MASK32))
                                              for addr in expected.command_addresses)
                    actual = {"instruction": step["output"][0], "error": bool(step["output"][1]),
                              "commands": tuple((c.target, c.address) for c in item["commands"])}
                    self.scoreboard.check(
                        expected={"instruction": expected.instruction, "error": expected.error,
                                  "commands": expected_commands}, actual=actual,
                        context=CheckContext(scoreboard=self.scoreboard.name, mode=ScoreboardMode.DIRECT,
                                             request=item["request"], accepted_event=item["accepted_event"],
                                             observed_event=step["event"]),
                    )
                    records.append({"pc": item["request"].pc, "accepted_tick": item["accepted_event"].tick,
                                    "completed_tick": step["event"].tick, **actual})
                    completed += 1
                if completed == len(requests):
                    break
            else:
                raise TimeoutError(f"stream completed {completed}/{len(requests)} in {timeout_cycles} cycles")
        finally:
            self._stream_candidate = None
            set_(self.dut.ifu_req_valid, 0)
            set_(self.dut.ifu_rsp_ready, 1)
        self.stream_records.extend(records)
        return records

    async def fetch(self, request: FetchRequest, *, timeout_cycles: int = 200) -> FetchTransaction:
        if timeout_cycles <= 0:
            raise ValueError("timeout_cycles must be positive")
        if not self._at_falling:
            await FallingEdge(self.dut.clk)
            self._at_falling = True
        expected = None
        command_start = len(self.commands)
        self._request_serial += 1
        self._request_command_ordinal = 0
        self._command_stall_left = request.command_stall
        self._second_command_stall = request.second_command_stall
        self._second_command_stall_applied = False
        self._response_delay = request.response_delay
        self._second_response_delay = (request.response_delay if request.second_response_delay is None
                                       else request.second_response_delay)
        self._error_uops = request.error_uops
        self._foreign_response = request.unselected_response
        self._command_was_stalled = False

        self.request_bits.drive({
            "pc": request.pc, "sequential": request.sequential,
            "seq_rv32": request.seq_rv32, "last_pc": request.effective_last_pc,
        })
        set_(self.dut.ifu2itcm_holdup, request.holdup)
        set_(self.dut.itcm_nohold, request.itcm_nohold)
        set_(self.dut.ifu_req_valid, 1)
        set_(self.dut.ifu_rsp_ready, 0 if request.response_stall else 1)

        accepted_tick: int | None = None
        completed_tick: int | None = None
        accepted_event = None
        completed_event = None
        actual_output: tuple[int, int] | None = None
        output_stall_left = request.response_stall
        stable_output: tuple[int, int] | None = None
        output_backpressured = False
        start_cycle = self._cycle
        self.active_request = {"pc": request.pc, "sequence_id": self._request_serial,
                               "accepted_tick": None, "last_tick": None, "commands": 0}

        for _ in range(timeout_cycles):
            step = await self._cycle_once()
            self.active_request.update(last_tick=step["event"].tick,
                                       commands=len(self.commands) - command_start)
            if step["request_accept"] and accepted_tick is None:
                accepted_event = step["event"]
                accepted_tick = accepted_event.tick
                expected = self.reference.accept(request)
                self.active_request["accepted_tick"] = accepted_tick
                set_(self.dut.ifu_req_valid, 0)
                # ifu_req_last_pc is a live view of the upstream pc_r, not a
                # payload that remains frozen for the whole transaction.  A
                # same-cross-holdup command consumes the old value in the
                # handshake cycle; a later second uop consumes this update.
                set_(self.dut.ifu_req_last_pc, request.pc)
            elif step["request_accept"]:
                raise AssertionError("one driven IFU request was accepted more than once")

            if stable_output is not None and not step["output_valid"]:
                raise AssertionError("IFU response valid withdrawn under backpressure")
            if step["output_valid"]:
                assert accepted_tick is not None, "IFU response arrived before request acceptance"
                payload = step["output"]
                assert payload is not None
                if stable_output is None:
                    stable_output = payload
                else:
                    assert payload == stable_output, (
                        "IFU response changed under backpressure: "
                        f"{stable_output} -> {payload}"
                    )
                if output_stall_left:
                    output_stall_left -= 1
                    output_backpressured = True
                    if output_stall_left == 0:
                        set_(self.dut.ifu_rsp_ready, 1)

            if step["output_accept"]:
                actual_output = step["output"]
                completed_event = step["event"]
                completed_tick = completed_event.tick
                break
        else:
            raise TimeoutError(
                f"IFU request pc={request.pc:#x} timed out after {timeout_cycles} cycles; "
                f"req_ready={get(self.dut.ifu_req_ready)}, "
                f"rsp_valid={get(self.dut.ifu_rsp_valid)}"
            )

        assert accepted_tick is not None
        assert completed_tick is not None
        assert actual_output is not None
        assert expected is not None
        set_(self.dut.ifu_rsp_ready, 1)

        observed = self.commands[command_start:]
        expected_targets = [IfuReferenceModel.target(address) for address in expected.command_addresses]
        expected_addresses = [
            address & 0xFFFF if target == "itcm" else address & MASK32
            for address, target in zip(expected.command_addresses, expected_targets)
        ]
        instruction, error = actual_output
        self.scoreboard.check(
            expected={
                "commands": tuple(zip(expected_targets, expected_addresses)),
                "instruction": expected.instruction, "error": expected.error,
            },
            actual={
                "commands": tuple((command.target, command.address) for command in observed),
                "instruction": instruction, "error": bool(error),
            },
            context=CheckContext(
                scoreboard="e203.fetch", mode=ScoreboardMode.DIRECT,
                sequence_id=self._request_serial, request=request,
                accepted_event=accepted_event, observed_event=completed_event,
            ),
        )

        latency = self._cycle - start_cycle
        transaction = FetchTransaction(
            pc=request.pc,
            target=expected.target,
            alignment=expected.alignment,
            lane_cross=expected.lane_cross,
            sequential=request.sequential,
            holdup=expected.lane_holdup,
            itcm_nohold=request.itcm_nohold,
            command_count=len(observed),
            command_backpressure=any(command.stalled for command in observed),
            response_backpressure=output_backpressured,
            response="error" if error else "ok",
            latency=latency,
            accepted_tick=accepted_tick,
            completed_tick=completed_tick,
            instruction=instruction,
            seq_rv32=request.seq_rv32,
            second_command_backpressure=any(c.ordinal == 1 and c.stalled for c in observed),
            error_pattern=sum(1 << c.ordinal for c in observed if c.ordinal in request.error_uops),
            route_switch=len({c.target for c in observed}) > 1,
            response_delay_order=(
                "not_split" if len(observed) != 2 else
                "equal" if self._response_delay == self._second_response_delay else
                "first_slower" if self._response_delay > self._second_response_delay else "second_slower"
            ),
        )
        self.transactions.append(transaction)
        self.active_request = None
        if self.coverage_enabled:
            self.coverage.sample(
                transaction,
                metadata={"pc": f"0x{request.pc:08x}", "tick": completed_tick},
                details=False,
            )
        trace(f"fetch {transaction}")
        return transaction

    async def finish(self, *, observe_cycles: int = 3) -> None:
        """Check a finite idle tail; the harness owns its protocol cycle stepping."""
        if self._pending or self._active is not None:
            raise AssertionError("e203 has incomplete ICB responses at finish")
        for _ in range(observe_cycles):
            step = await self._cycle_once()
            assert step["command"] is None, "extra ICB command after the last fetch"
            assert not step["output_valid"], "extra IFU response after the last fetch"

    def summary(self, elapsed_seconds: float) -> dict[str, Any]:
        half_ticks = int(self.dut.GetXClock().GetHalfTick())
        return {
            "transactions": len(self.transactions),
            "commands": len(self.commands),
            "checked": self.scoreboard.status.checked,
            "active_request": self.active_request,
            "half_ticks": half_ticks,
            "elapsed_seconds": elapsed_seconds,
            "transactions_per_second": (
                len(self.transactions) / elapsed_seconds if elapsed_seconds else 0.0
            ),
            "half_ticks_per_second": half_ticks / elapsed_seconds if elapsed_seconds else 0.0,
            "functional_coverage": (
                self.coverage.coverage if self.coverage_enabled else None
            ),
        }


def directed_requests() -> list[FetchRequest]:
    """A deterministic sequence covering route, split, hold and error paths."""

    requests = [
        FetchRequest(ITCM_BASE + 0x100, response_delay=0),
        FetchRequest(
            ITCM_BASE + 0x102,
            sequential=True,
            last_pc=ITCM_BASE + 0x100,
            holdup=True,
        ),
        FetchRequest(
            ITCM_BASE + 0x104,
            sequential=True,
            last_pc=ITCM_BASE + 0x102,
            holdup=True,
            response_stall=2,
        ),
        FetchRequest(
            ITCM_BASE + 0x106,
            sequential=True,
            last_pc=ITCM_BASE + 0x104,
            holdup=True,
            command_stall=2,
            response_delay=2,
        ),
        FetchRequest(ITCM_BASE + 0x206, error_uops=frozenset({0})),
        FetchRequest(
            ITCM_BASE + 0x306,
            itcm_nohold=True,
            holdup=True,
            error_uops=frozenset({1}),
        ),
        FetchRequest(0x0000_0100),
        FetchRequest(0x0000_0102, error_uops=frozenset({0})),
        FetchRequest(0x0000_0202, command_stall=1, response_stall=3),
        FetchRequest(0x0000_0300, response_delay=15),
    ]
    # A +4 sequential fetch needs last_pc+6 on the held-lane crossing path.
    requests.extend([
        FetchRequest(ITCM_BASE + 0x502),
        FetchRequest(ITCM_BASE + 0x506, sequential=True, seq_rv32=True,
                     last_pc=ITCM_BASE + 0x502, holdup=True),
        FetchRequest(ITCM_BASE + 0x508, sequential=True,
                     last_pc=ITCM_BASE + 0x506, holdup=True),
    ])
    # Both routes exercise WAIT2ND and all first/second error combinations.
    for base in (ITCM_BASE + 0x1000, 0x0000_1000):
        crossing = 6 if base & ITCM_BASE else 2
        for index, errors in enumerate((frozenset(), frozenset({0}),
                                        frozenset({1}), frozenset({0, 1}))):
            requests.append(FetchRequest(
                base + index * 0x100 + crossing,
                second_command_stall=3, error_uops=errors,
            ))
    requests.extend([
        FetchRequest(ITCM_BASE + 0xFFFE),  # ITCM -> BIU across the region edge.
        FetchRequest(ITCM_BASE - 2),       # BIU -> ITCM across the region edge.
        FetchRequest(0xFFFF_FFFE),         # 32-bit address wrap in the second uop.
    ])
    # Reuse, then explicitly disable reuse, and recover after a split error.
    base = ITCM_BASE + 0x6000
    requests.extend([
        FetchRequest(base),
        FetchRequest(base + 2, sequential=True, last_pc=base, holdup=True),
        FetchRequest(base + 4, sequential=True, last_pc=base + 2,
                     holdup=True, itcm_nohold=True),
        FetchRequest(base + 6, sequential=True, last_pc=base + 4,
                     holdup=True, itcm_nohold=True, second_command_stall=3,
                     second_response_delay=8, response_stall=6, error_uops=frozenset({1})),
        FetchRequest(base + 8, sequential=True, last_pc=base + 6, holdup=True),
        FetchRequest(base + 0x42, holdup=True),  # Redirect must fetch despite holdup.
    ])
    for pc in (ITCM_BASE + 0x7006, 0x0000_7002):
        for first_delay, second_delay in ((0, 9), (9, 0)):
            requests.append(FetchRequest(
                pc, command_stall=2, second_command_stall=2, response_stall=4,
                response_delay=first_delay, second_response_delay=second_delay,
            ))
    return requests


def random_requests(seed: int, count: int) -> list[FetchRequest]:
    rng = random.Random(seed)
    result: list[FetchRequest] = []
    previous_pc: int | None = None
    for _ in range(count):
        target = "itcm" if rng.random() < 0.5 else "biu"
        base = ITCM_BASE + 0x1000 if target == "itcm" else 0x0001_0000
        lane = 8 if target == "itcm" else 4
        offset = rng.choice(range(0, lane, 2))
        pc = base + rng.randrange(0, 0x400, lane) + offset
        sequential = previous_pc is not None and rng.random() < 0.35
        if sequential:
            # Keep the input relation legal and occasionally reuse the held
            # ITCM lane.  The DUT's interface provides last_pc explicitly.
            step = rng.choice((2, 4))
            pc = (previous_pc + step) & MASK32
            if IfuReferenceModel.target(pc) != target:
                sequential = False
        holdup = target == "itcm" and sequential and rng.random() < 0.65
        errors = frozenset({rng.randrange(2)}) if rng.random() < 0.08 else frozenset()
        result.append(
            FetchRequest(
                pc,
                sequential=sequential,
                seq_rv32=sequential and (pc - (previous_pc or pc) == 4),
                last_pc=previous_pc if sequential else pc,
                holdup=holdup,
                itcm_nohold=target == "itcm" and rng.random() < 0.1,
                command_stall=rng.randrange(0, 4),
                second_command_stall=rng.randrange(0, 4),
                response_delay=rng.randrange(0, 5),
                second_response_delay=rng.randrange(0, 8),
                response_stall=rng.randrange(0, 4),
                error_uops=errors,
            )
        )
        previous_pc = pc
    return result


async def run_campaign(
    dut: Any,
    *,
    seed: int,
    random_count: int,
    coverage_enabled: bool = True,
    artifact_dir: str | Path | None = None,
    data_pattern: str = "address",
) -> tuple[E203Harness, dict[str, Any]]:
    dut.InitClock("clk")
    harness = E203Harness(dut, seed=seed, coverage_enabled=coverage_enabled, data_pattern=data_pattern)
    harness.initialize_inputs()
    backend = XCommClockBackend(dut.GetXClock())
    started = time.perf_counter()
    try:
        async with Execution(backend, max_batch_ticks=128, quantum_ms=5.0) as execution:
            harness.internal.start(execution)
            try:
                await harness.reset()
                for request in directed_requests():
                    await harness.fetch(request)
                for request in random_requests(seed, random_count):
                    await harness.fetch(request)
                await harness.finish()
            finally:
                await harness.internal.aclose()
    finally:
        primary = sys.exception()
        errors = []
        try:
            harness.idle_inputs()
        except BaseException as error:
            errors.append(error)
        try:
            backend.close()
        except BaseException as error:
            errors.append(error)
        summary = harness.summary(time.perf_counter() - started)
        summary.update(seed=seed, random_count=random_count, data_pattern=data_pattern,
                       state="failed" if primary or errors else "passed")
        if primary is not None:
            summary["error"] = f"{type(primary).__name__}: {primary}"
        if errors:
            summary["cleanup_errors"] = [repr(error) for error in errors]
        if artifact_dir is not None:
            try:
                write_artifacts(harness, summary, artifact_dir)
            except Exception as error:
                errors.append(error)
        if errors:
            if primary is not None:
                for error in errors:
                    primary.add_note(f"e203 cleanup/artifact failure: {error!r}")
            else:
                raise BaseExceptionGroup("e203 cleanup/artifact failures", errors)
    return harness, summary


def write_artifacts(
    harness: E203Harness,
    summary: dict[str, Any],
    artifact_dir: str | Path,
) -> dict[str, Path]:
    root = Path(artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    functional = root / "functional-coverage.json"
    CoverageDatabase([harness.coverage]).write_json(functional)
    harness.internal.checks.write(root)
    transactions = root / "transactions.json"
    transactions.write_text(
        json.dumps([asdict(item) for item in harness.transactions], indent=2) + "\n",
        encoding="utf-8",
    )
    commands = root / "commands.json"
    commands.write_text(
        json.dumps([asdict(item) for item in harness.commands], indent=2) + "\n",
        encoding="utf-8",
    )
    performance = root / "performance.json"
    performance.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return {
        "functional_coverage": functional,
        "internal_coverage": root / "internal-functional-coverage.json",
        "internal_checks": root / "internal-checks.json",
        "transactions": transactions,
        "commands": commands,
        "performance": performance,
    }


def convert_line_coverage(
    coverage_dat: str | Path,
    artifact_dir: str | Path,
) -> tuple[Path, dict[str, Any]]:
    source = Path(coverage_dat)
    if not source.is_file():
        raise FileNotFoundError(f"Verilator coverage data not found: {source}")
    root = Path(artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    info = root / "line-coverage.info"
    subprocess.run(
        ["verilator_coverage", "--write-info", str(info), str(source)],
        check=True,
        text=True,
        capture_output=True,
    )
    parsed = parse_lcov(info, name="Verilator")
    summary = {
        "format": parsed["format"],
        "line_coverage_file": str(info),
        "lines_found": parsed["lines_found"],
        "lines_hit": parsed["lines_hit"],
        "line_coverage": parsed["line_coverage"],
    }
    summary_path = root / "line-coverage.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return info, summary
