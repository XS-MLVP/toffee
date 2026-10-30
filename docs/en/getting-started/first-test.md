# 1. Your first PASS and FAIL

Learn how a test judges a result and where to look when it fails. This lesson does not introduce clocks or verification components.

## A four-line test

Open `examples/getting_started/test_accumulator.py` and find `test_addition` near the beginning.
The file already contains later lessons; focus on these lines:

<!-- executable-example: examples/getting_started/test_accumulator.py#assertion -->
```python
def test_addition():
    expected = 8
    actual = 3 + 5
    assert actual == expected
```

`def` defines a function. pytest discovers functions whose names begin with `test_`.
Use four spaces for indentation. `=` assigns a value; `==` compares values.
`assert` requires a condition to hold and fails the test otherwise.
Here `3 + 5` stands in for the computation under test; the next lesson reads the actual value from a DUT.

Run in the terminal:

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_addition
```

Expect output similar to this, with a different duration:

```text
.                                                                        [100%]
1 passed in ...
```

`.` means a passing test and `-q` requests concise output. `::test_addition` selects just that function.
Running the file with Python alone does not automatically call its test functions.

## Make it fail deliberately

Change `expected = 8` to `expected = 9` and repeat the command:

```text
E       assert 8 == 9
1 failed in ...
```

The actual result is 8, while the test expected 9. Check the specification and test data before
blaming the DUT. Restore 8 and confirm that the test passes again.

## Add a check

Append this function to the same file:

```python
def test_eight_bit_wraparound():
    actual = (250 + 10) % 256
    assert actual == 4
```

`%` computes a remainder. An unsigned 8-bit value holds 0–255; retaining the low 8 bits of 260 gives 4.
Run the new function:

```bash
python3 -m pytest -q examples/getting_started/test_accumulator.py::test_eight_bit_wraparound
```

Expect `1 passed`. This still checks the specification without accessing a DUT.
Keep working in the same file and continue with [inputs and clocks](clock-and-input.md).
