"""Exercise the documented drivers against sampled pins on both backends."""

import asyncio
from collections import Counter

import pytest

from examples.transactions.pipeline import (
    Input, QueuedStages, Response, drive_stages, run_responses, run_stages,
)
from toffee import (
    AsyncSingleCycleDriver, ClockCycles, Execution, ScoreboardMismatch, ScoreboardTimeoutError,
    SyncDriver, TransferState,
)


@pytest.mark.asyncio
async def test_continuous_inputs_keep_three_responses_in_flight(make_toy):
    toy = make_toy()
    tasks_before = asyncio.all_tasks()
    async with asyncio.timeout(2):
        transfers = await run_responses(toy, response_timeout_cycles=3)
    # Observe pins as well as driver notifications. max_active remains 1.
    assert toy.accepted == [(1, 2), (2, 4), (3, 6)]
    assert [sample[1] for sample in toy.samples[:4]] == [1, 2, 3, 0]
    assert [(r.tag, tick) for r, tick in toy.outputs] == [(1, 8), (2, 10), (3, 12)]
    assert max(tick for _, tick in toy.accepted) < toy.outputs[0][1]
    assert [t.completed_event.tick - t.accepted_event.tick for t in transfers] == [6, 6, 6]
    assert all(t.state is TransferState.COMPLETED for t in transfers)
    assert toy.monitor.closed
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
@pytest.mark.parametrize("submission", ["direct", "queued", "send"])
@pytest.mark.parametrize("max_active", [1, 2])
async def test_stages_overlap_in_sampled_cycles_and_arbitrate_resources(
    make_toy, submission, max_active,
):
    toy = make_toy(staged=True, respond=False)
    tasks_before = asyncio.all_tasks()
    async with asyncio.timeout(2):
        if submission == "direct":
            # Compatibility regression for low-level concurrent callers. Daily
            # examples use Driver-owned submissions instead of user tasks.
            class DirectStages(SyncDriver[int]):
                def __init__(self):
                    self.clock, self.first, self.second = toy.clock, toy.first, toy.second
                    self.second_cycles = toy.second_cycles
                    super().__init__((self.first, self.second), name="direct", max_active=max_active)

                async def _drive_one(self, request):
                    return await drive_stages(self, request)

            async with Execution(toy.backend), DirectStages() as driver:
                async with asyncio.TaskGroup() as group:
                    calls = [group.create_task(driver.send(request)) for request in (1, 2, 3)]
                accepted = [(request, call.result().tick) for request, call in zip((1, 2, 3), calls)]
        else:
            accepted = await run_stages(toy, wait_each=submission == "send", max_active=max_active)
    serial = max_active == 1 or submission == "send"
    assert accepted == (
        [(1, 8), (2, 16), (3, 24)] if serial
        else [(1, 8), (2, 14), (3, 20)]
    )
    overlap = [(tick, first, second) for tick, first, second in toy.samples if first and second]
    assert overlap == ([] if serial else [(4, 2, 1), (10, 3, 2)])
    # Each request occupies the first port once and the second for three samples.
    assert [first for _, first, _ in toy.samples if first] == [1, 2, 3]
    assert Counter(second for _, _, second in toy.samples if second) == {1: 3, 2: 3, 3: 3}
    assert [second for _, _, second in toy.samples if second] == [1] * 3 + [2] * 3 + [3] * 3
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
async def test_fifo_accepts_variable_ordered_response_latency(make_toy):
    toy = make_toy(delays={1: 3, 2: 3, 3: 4})
    async with asyncio.timeout(2):
        await run_responses(toy, association="fifo")
    assert [(r.tag, tick) for r, tick in toy.outputs] == [(1, 8), (2, 10), (3, 14)]


@pytest.mark.asyncio
async def test_keyed_responses_complete_in_reverse_order(make_toy):
    toy = make_toy(delays={1: 5, 2: 3, 3: 1})
    async with asyncio.timeout(2):
        transfers = await run_responses(toy, association="key")
    assert toy.accepted == [(1, 2), (2, 4), (3, 6)]
    assert [(r.tag, tick) for r, tick in toy.outputs] == [(3, 8), (2, 10), (1, 12)]
    assert [t.completed_event.tick for t in transfers] == [12, 10, 8]
    assert [await t for t in transfers] == [Response(1, 10), Response(2, 20), Response(3, 30)]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "late", "wrong-fifo", "capacity"])
async def test_pipeline_failures_are_not_silent_and_clean_up(make_toy, failure):
    tasks_before = asyncio.all_tasks()
    if failure == "wrong-fifo":
        toy = make_toy(delays={1: 5, 2: 3, 3: 1})
        options = {"association": "fifo"}
        error_type = ScoreboardMismatch
    elif failure == "capacity":
        toy = make_toy()
        options = {"capacity": 2}
        error_type = RuntimeError
    else:
        toy = make_toy(delays={1: 4}, drop=(1,) if failure == "missing" else ())
        options = {
            "requests": (1,),
            "association": "fifo" if failure == "missing" else "fixed",
            "response_timeout_cycles": 2 if failure == "missing" else 6,
        }
        error_type = ScoreboardTimeoutError

    async with asyncio.timeout(2):
        with pytest.raises(error_type) as caught:
            await run_responses(toy, **options)
    if failure in {"missing", "late"}:
        assert caught.value.operation == ("response" if failure == "missing" else "fixed_latency")
        assert caught.value.context.request == 1
        assert caught.value.budget_cycles == (2 if failure == "missing" else 3)
    elif failure == "wrong-fifo":
        assert caught.value.expected == Response(1, 10)
        assert caught.value.actual == Response(3, 30)
    else:
        assert "capacity is full" in str(caught.value)
    assert toy.monitor.closed
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
@pytest.mark.parametrize("max_active", [1, 2])
async def test_submitted_inputs_complete_at_acceptance_without_response(make_toy, max_active):
    toy = make_toy(staged=True, respond=False)
    tasks_before = asyncio.all_tasks()
    async with asyncio.timeout(2), Execution(toy.backend) as execution:
        async with QueuedStages(toy, max_active=max_active) as driver:
            transfers = [driver.send(request) for request in (1, 2, 3)]
            assert all(t.state is TransferState.PENDING for t in transfers)
            events = [await transfer for transfer in transfers]
            assert [event.tick for event in events] == (
                [8, 16, 24] if max_active == 1 else [8, 14, 20]
            )
            assert [(t.request, e.tick) for t, e in zip(transfers, events)] == toy.accepted
            assert all(t.state is TransferState.COMPLETED for t in transfers)
            assert all(t.accepted_event is t.completed_event is e
                       for t, e in zip(transfers, events))
            assert not driver.has_outstanding and driver._accepted.empty()
        assert not execution.reactor._drive_owners
        assert not driver._tasks and not driver._pending and driver._worker is None
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
async def test_input_batches_reuse_capacity_and_keep_adjacent_cycles(make_toy):
    toy = make_toy(respond=False)
    async with asyncio.timeout(2), Execution(toy.backend):
        async with Input(toy, capacity=1) as driver:
            for request in (1, 2, 3, 4):
                transfer = driver.send(request)
                with pytest.raises(RuntimeError, match="capacity is full"):
                    driver.send(99)
                await transfer
                assert not driver.has_outstanding and driver._accepted.empty()
    assert toy.accepted == [(1, 2), (2, 4), (3, 6), (4, 8)]


@pytest.mark.asyncio
async def test_input_completion_does_not_enter_response_acceptance_stream(make_toy):
    toy = make_toy(staged=True, respond=False)
    async with asyncio.timeout(2), Execution(toy.backend):
        async with QueuedStages(toy, max_active=2) as driver:
            response_transfer = driver.submit(1)
            input_transfer = driver.send(2)
            assert (await input_transfer).tick == 14
            assert response_transfer.state is TransferState.PROCESSING
            assert await driver.recv_accepted() is response_transfer
            assert driver._accepted.empty()
            response_transfer.complete(response_transfer.accepted_event, Response(1, 10))
            assert await response_transfer == Response(1, 10)
            assert not driver.has_outstanding


@pytest.mark.asyncio
@pytest.mark.parametrize("max_active", [1, 2])
@pytest.mark.parametrize("started", [False, True])
async def test_cancel_input_releases_queue_and_resources(make_toy, max_active, started):
    toy = make_toy(staged=True, respond=False)
    tasks_before = asyncio.all_tasks()
    async with asyncio.timeout(2), Execution(toy.backend) as execution:
        async with QueuedStages(toy, max_active=max_active) as driver:
            async with execution.paused():
                cancelled = driver.send(1)
                following = driver.send(2)
                if started:
                    await asyncio.sleep(0)
                cancelled.cancel()
            await following
            assert cancelled.state is TransferState.CANCELLED
            assert toy.accepted == [(2, 8)]
            assert not driver.has_outstanding
            assert not driver.resource_lock("first").locked()
            assert not driver.resource_lock("second").locked()
        assert not execution.reactor._drive_owners
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
async def test_cancelling_input_waiter_leaves_submission_running(make_toy):
    toy = make_toy(staged=True, respond=False)
    async with asyncio.timeout(2), Execution(toy.backend) as execution:
        async with QueuedStages(toy, max_active=1) as driver:
            transfer = driver.send(1)

            async def wait_input():
                return await transfer

            async with execution.paused():
                waiter = asyncio.create_task(wait_input())
                await asyncio.sleep(0)
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
                assert transfer.state is TransferState.PENDING
            assert (await transfer).tick == 8
            assert transfer.state is TransferState.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", [False, True])
async def test_input_failure_reported_by_waiter_or_context_once(make_toy, observed):
    class FailingInput(QueuedStages):
        async def _drive_one(self, request):
            await ClockCycles(self.clock, 1)
            raise ValueError("input failed")

    toy = make_toy(staged=True, respond=False)
    tasks_before = asyncio.all_tasks()
    async with asyncio.timeout(2), Execution(toy.backend) as execution:
        async def scenario():
            async with FailingInput(toy, max_active=1) as driver:
                first, second = driver.send(1), driver.send(2)
                if observed:
                    with pytest.raises(ValueError, match="input failed"):
                        await first
                else:
                    await ClockCycles(toy.clock, 2)
                assert first.state is second.state is TransferState.FAILED
                assert not driver.has_outstanding

        if observed:
            await scenario()
        else:
            with pytest.raises(ValueError, match="input failed"):
                await scenario()
        assert not execution.reactor._drive_owners
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
@pytest.mark.parametrize("driver_kind", ["serial", "overlap", "single_cycle"])
async def test_send_runs_without_waiter_and_reawait_does_not_resubmit(make_toy, driver_kind):
    toy = make_toy(staged=driver_kind != "single_cycle", respond=False)
    if driver_kind == "single_cycle":
        driver = AsyncSingleCycleDriver(toy.clock, toy.first, idle=0, capacity=3)
        ticks = [2, 4, 6]
    else:
        driver = QueuedStages(toy, max_active=1 if driver_kind == "serial" else 2)
        ticks = [8, 16, 24] if driver_kind == "serial" else [8, 14, 20]

    async with asyncio.timeout(2), Execution(toy.backend):
        async with driver:
            # The keyword is part of the existing send contract. No await or
            # user-created task is needed to start any of these inputs.
            inputs = [driver.send(transaction=request) for request in (1, 2, 3)]
            await ClockCycles(toy.clock, 20)
            assert all(item.state is TransferState.COMPLETED for item in inputs)
            first_read = [await item for item in inputs]
            second_read = [await item for item in inputs]
            assert all(first is second for first, second in zip(first_read, second_read))
            assert [event.tick for event in first_read] == ticks
            assert toy.accepted == list(zip((1, 2, 3), ticks))
            assert not driver.has_outstanding and driver._accepted.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_active", [1, 2])
@pytest.mark.parametrize("exit_kind", ["normal", "failure", "cancel"])
async def test_input_context_exit_cleans_unaccepted_work(make_toy, max_active, exit_kind):
    toy = make_toy(staged=True, respond=False)
    tasks_before = asyncio.all_tasks()
    async with asyncio.timeout(2), Execution(toy.backend) as execution:
        driver = QueuedStages(toy, max_active=max_active)
        inputs = []
        entered = asyncio.Event()

        async def scenario():
            async with driver:
                inputs.extend(driver.send(request) for request in (1, 2, 3))
                await ClockCycles(toy.clock, 1)
                entered.set()
                if exit_kind == "failure":
                    raise LookupError("body failed")
                if exit_kind == "cancel":
                    await ClockCycles(toy.clock, 100)

        if exit_kind == "cancel":
            owner = asyncio.create_task(scenario())
            try:
                await entered.wait()
                owner.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await owner
            finally:
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
        elif exit_kind == "failure":
            with pytest.raises(LookupError, match="body failed"):
                await scenario()
        else:
            await scenario()
        assert all(item.state is TransferState.CANCELLED for item in inputs)
        assert not toy.accepted
        assert not driver.has_outstanding and not driver._pending and not driver._tasks
        assert driver._worker is None and not execution.reactor._drive_owners
        assert not driver.resource_lock("first").locked()
        assert not driver.resource_lock("second").locked()
        with pytest.raises(RuntimeError, match="not started"):
            driver.send(4)
    assert asyncio.all_tasks() == tasks_before


@pytest.mark.asyncio
@pytest.mark.parametrize("quiesce", [False, True])
async def test_single_cycle_callable_idle_preserves_policy_until_explicit_quiesce(make_toy, quiesce):
    toy = make_toy(respond=False)
    idle_inputs = []

    def idle(previous):
        idle_inputs.append(previous)
        return 0 if previous is None else previous["data"]

    try:
        async with Execution(toy.backend), AsyncSingleCycleDriver(
            toy.clock, toy.first, idle=idle, encoder=lambda request: request["data"],
        ) as driver:
            await driver.send({"data": 7})
            assert toy.first.U() == 7
            if quiesce:
                driver.quiesce()
        assert toy.first.U() == (0 if quiesce else 7)
        assert idle_inputs == [None, {"data": 7}] + ([None] if quiesce else [])
    finally:
        # This scenario deliberately uses a nonzero idle value; fixture starts fresh.
        toy.first.Set(0)
