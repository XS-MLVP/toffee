# Start with your first test

Use an **8-bit accumulator** to progress from one assertion to pipelined inputs, a reference model,
and coverage. Run each example, then change one thing and observe the result.
The first lessons use a Python DUT, so no RTL simulator is needed. Real DUT integration comes later.

## Find the code

The two main files are:

```text
examples/getting_started/
├── test_accumulator.py   按教学顺序排列的测试、驱动、监视器和模型
└── toy_dut.py            已写好的累加器
```

Each lesson names the relevant test and gives a command that runs only that test.
When using the repository files, do not paste duplicate definitions into them. When starting from
an empty file, append each lesson in order and retain earlier imports and definitions.
`# region` and `# endregion` are editor-folding comments; they do not affect execution.

## Follow the lessons

| Lesson | Question it answers |
| --- | --- |
| [0. Prepare the environment](installation.md) | Where do commands run, and how are dependencies installed? |
| [1. Your first PASS and FAIL](first-test.md) | How does a test decide whether a result is correct? |
| [2. Inputs and clocks](clock-and-input.md) | Why has the result not changed after writing an input? |
| [3. Data sets and boundaries](more-cases.md) | What should be tested beyond one ordinary value? |
| [4. Extract a Driver](driver.md) | How can input timing be defined once and reused? |
| [5. Delayed outputs and Monitors](monitor.md) | How can a one-cycle output be captured reliably? |
| [6. Assemble an Agent](agent-test.md) | How do input, output, and checking fit together? |
| [7. Models and fixtures](reference-model.md) | How are expectations computed and test environments isolated? |
| [8. Pipelines, batches, and cancellation](pipeline.md) | Can the next request start before the previous response arrives? |
| [9. Understand failures](failures.md) | What do wrong, missing, late, and extra responses look like? |
| [10. Functional coverage](coverage.md) | Which planned scenarios remain untested? |

You can stop after lesson 3 to practice. Driver, Monitor, and Agent organize code you have already
written; you do not need all three in your first test. Afterwards, try [out-of-order responses](out-of-order.md)
and [native clocks and project DUTs](native-and-next.md).

## Four terms

- **DUT**: the design under test, such as an accumulator, cache, bus interface, or arithmetic unit.
- **Stimulus**: an input sent to the DUT, such as “add 3.”
- **Expected and actual values**: the result derived from the specification and the result observed from the DUT.
- **Transaction**: a complete operation, such as adding 3 and receiving its result, possibly across several cycles.

Run `bash` blocks in a terminal; `python` blocks belong in source files. Do not copy expected output as commands.
Continue with [installation](installation.md).

## After the tutorial

After lesson 3 you can write clocked tests with boundary assertions. After lesson 10 you can combine
components, models, pipeline checking, and coverage. Then use the [how-to guides](../guides/index.md),
[API reference](../reference/api.md), and [explanations](../explanation/index.md) for your own project.
