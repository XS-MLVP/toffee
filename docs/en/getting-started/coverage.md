# 10. Know which scenarios were exercised

Separate “all comparisons performed were correct” from “all planned scenarios occurred.”
Tests using 3, 5, and 7 may all pass without observing a result of 0 or 255.

## Define three coverage targets

Classify **successfully checked responses** into bins:

| Bin | Result range | Stimulus |
| --- | --- | --- |
| zero | 0 | Start at 0 and add 0 |
| maximum | 255 | Then add 255 |
| middle | 1–254 | Then add 2, overflowing to 1 |

Hitting all three means 100% of this plan, not all inputs, timing combinations, or design behavior.
Add goals and tests when the specification requires more.

## Collect and save coverage

Continue in `examples/getting_started/test_accumulator.py`:

<!-- executable-example: examples/getting_started/test_accumulator.py#coverage -->
```python
from toffee import Bin, CoverGroupDef, CoverPointDef, CoverageDatabase


@pytest.mark.asyncio
async def test_result_coverage(tmp_path):
    definition = CoverGroupDef("accumulator", (
        CoverPointDef("result", {
            "zero": Bin.values(0),
            "middle": Bin.range(1, 254),
            "maximum": Bin.values(255),
        }),
    ))
    coverage = definition.instantiate("tutorial")
    dut = TutorialDut()
    agent = make_pipeline_agent(dut)
    agent.connect(AccumulatorModel())
    try:
        async with Execution(dut.backend, agents=[agent]):
            for operand in [0, 255, 2]:
                transfer = agent.submit(operand)
                result = await transfer
                coverage.sample({"result": result})
            await agent.finish(timeout_cycles=10, observe_cycles=1)
        coverage.assert_coverage(100)
        print(f"Result bins: {coverage.coverage:.0f}%")
    finally:
        report = tmp_path / "functional.json"
        try:
            CoverageDatabase([coverage]).write_json(report)
            print(f"Coverage JSON: {report}")
        finally:
            dut.close()
```

```bash
python3 -m pytest -q -s examples/getting_started/test_accumulator.py::test_result_coverage
```

Expected output:

```text
Result bins: 100%
Coverage JSON: /.../test_result_coverage0/functional.json
...
1 passed in ...
```

`-s` displays print output. pytest supplies the `tmp_path` fixture; `/ "functional.json"` selects a file within it.
`CoverGroupDef` defines the plan; `CoverPointDef("result", ...)` defines classification.
`instantiate("tutorial")` creates counters and `sample({"result": result})` records a sample.
Count after `await transfer` has checked the response, rather than treating submitted input as completed work.

This example waits for each response to make sampling explicit. For throughput tests use the submission
pattern from lesson 8. Do not consume the Agent's response Monitor separately.
The `finally` block saves observations even on a comparison or coverage failure, then closes the DUT.

## Leave one bin uncovered

Replace `[0, 255, 2]` with `[0, 255]`. Comparisons pass, but `coverage.assert_coverage(100)` fails at
about 66.67%, identifying the missing `middle` bin. Restore the list to reach 100%.

## Generate HTML

Replace the placeholder path with the JSON path printed by the previous command:

```bash
toffee-coverage-report --functional '/实际打印的路径/functional.json' --output tutorial-coverage.html
```

Open `tutorial-coverage.html` in the repository root. Functional coverage needs no RTL line-coverage tools.
For richer bins, crosses, and merging, see [coverage](../guides/coverage.md).
Test outcomes and functional coverage answer different questions; see [coverage across test cases](../guides/functional-points.md).

You can now drive, sample, model, finish, and save results. Continue with the optional
[out-of-order](out-of-order.md) or [native integration](native-and-next.md) lessons.
