# Toffee user manual

This manual describes Toffee 2, migrated from XReactor. Legacy Toffee remains on
[`master`](https://github.com/XS-MLVP/toffee/tree/master); see the [migration guide](guides/toffee-v2-migration.md).

A hardware test supplies inputs, waits for results, and checks them against the specification.
Toffee expresses these operations as Python tests and helps organize repeated behavior into reusable components.
Choose the section that matches your current task; each section can be read independently.

| Your goal | Start here | What you will learn |
| --- | --- | --- |
| Follow a first exercise | [Tutorials](getting-started/index.md) | Test an 8-bit accumulator with assertions, drivers, automatic checking, and coverage |
| Complete a project task | [How-to guides](guides/index.md) | Connect a DUT, assemble verification components, run regressions, or migrate a project |
| Look up an interface | [Reference](reference/index.md) | Find APIs, defaults, CLI options, and supported behavior |
| Understand the design | [Explanation](explanation/index.md) | Understand simulation progress, resource ownership, and what coverage proves |

## Write your first test

Start with [installation](getting-started/installation.md) and [your first PASS and FAIL](getting-started/first-test.md).
The first exercises use Python without an RTL simulator. Every lesson identifies its source file,
command, and expected result, introducing concepts as the exercise needs them.

## Common tasks

- [Configure simulation](guides/execution-and-backend.md): create a backend and release its resources.
- [Drive inputs](guides/drivers.md) and [capture outputs](guides/monitors.md): reuse interface timing.
- [Connect a reference model](guides/agents-and-reference-models.md): advance it at acceptance and compare responses.
- [Run regressions](guides/runner.md): select tests, use multiple workers, and diagnose collection failures.

See [runnable examples](examples.md) for complete programs and the [feature map](reference/feature-map.md) for implementation and regression links.
