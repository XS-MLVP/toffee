# Checking, coverage, and regression results

A regression can produce a test verdict, functional coverage, native coverage, and a run manifest.
Each answers a different question and should be read separately.

## Observing a scenario does not establish correctness

Assertions and Scoreboards compare expected and actual behavior. Functional coverage records
whether defined scenarios were observed. Native coverage records simulator code or signal activity.
High coverage does not prove complete checking, and passing tests do not prove all required
scenarios have occurred.

For example, a Monitor may sample coverage for a response before the Scoreboard finds incorrect
data. The response was observed and was wrong; both facts remain valid. Define the sampling
location precisely: input acceptance, DUT output, or successful checking. Different locations
make counts describe different facts.

## Deliver and sample an observation

Checking and coverage often need the same DUT observation. Two consumers independently calling
one Monitor's `recv()` can split the stream so each sees only part of it.

A project can sample the same immutable Transfer once before delivering it to the checker.
This preserves a single consumption path and an explicit source. Undelivered observations after
checking stops do not count. Projects needing observation after failure must separately define
that lifecycle. See the [verification environment recipe](../guides/verification-flow.md).

## Aggregate consistent measurements across cases

The Runner collects groups created by each test after teardown, and shared groups once at process
exit. Counts can be added across processes only when definitions, instance identities, and sampling
rules are compatible. Zero-hit bins must remain present so unobserved scenarios cannot disappear
from the denominator.

Unit tests for a coverage model may fill bins artificially to verify classification. Those samples
describe the model, not scenarios observed on a DUT. The Runner's exclusion marker and `capture=False`
keep model self-tests separate from DUT measurements. See [run tests and aggregate coverage](../guides/runner.md).

Native counts merge by DUT build identity, never across different builds. Functional and native
coverage remain separate; neither becomes a combined score that replaces the test verdict.

## Distinguish complete reports from passing tests

`state="committed"` in `manifest.json` means artifacts were fully written and validated. It does
not mean every test passed. A completely executed regression containing assertion failures can
still produce useful diagnostic coverage, while preserving a nonzero test exit code.

Crashes, incomplete collection, or corrupt inputs make aggregation incomplete. Available
intermediate inputs help diagnosis but do not constitute a complete regression. Cleanup failure
after commit is different: final artifacts remain, the manifest records `cleanup_error`, and the
command still reports failure.

See [Runner reference](../reference/runner.md) for files, states, and exit behavior, and
[the operating guide](../guides/runner.md) for troubleshooting steps.
