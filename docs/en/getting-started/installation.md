# 0. Prepare an environment for the examples

By the end of this page, you should be able to import Toffee in a terminal.
The commands use a Linux/macOS shell and require **Python 3.11 or newer**. No hardware simulator is needed yet.

## Enter the repository root

Use the `toffee` directory containing `pyproject.toml`, `examples/`, and `docs/`. To obtain it:

```bash
git clone --branch main https://github.com/XS-MLVP/toffee.git
cd toffee
```

Run all following commands from that directory, rather than from `src/` or `examples/`.
The distribution is named `uc-toffee`; the Python import is `toffee`.
`python3 -m pip install "uc-toffee[runner]"` also installs `uc-xcomm>=1.1,<2`, which supplies `xspcomm`.
The old `pytoffee` package belongs to Legacy Toffee.

The tutorial installs from source so the repository examples are available. When developing against
unreleased native changes, prepare the pinned dependency described in the
[contribution guide](https://github.com/XS-MLVP/toffee/blob/main/CONTRIBUTING.md).

## Install in a virtual environment

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -c 'import toffee; print(toffee.__file__)'
```

The last command should print this checkout's `src/toffee/__init__.py`.
`venv` creates `.venv`; `source` selects it in the current shell. The editable `-e` installation makes
source changes immediately visible. Keep the quotes around `.[test]`.
In a new terminal, enter the repository and run `source .venv/bin/activate` again.

pytest discovers and runs tests; pytest-asyncio runs the later asynchronous tests.
You do not need to create an event loop or TaskGroup yourself.

## Troubleshoot installation

| Symptom | Check |
| --- | --- |
| Python is older than 3.11 | Install a supported interpreter and recreate `.venv` |
| Missing `venv` or `ensurepip` | Install the distribution's Python venv component |
| pip cannot download packages | Check network access and the configured package mirror |
| `No module named toffee` | Activate the environment and use the same interpreter for installation and execution |
| Missing `examples` or a test file | Return to the repository root and use `python3 -m pytest ...` |

Continue with [your first PASS and FAIL](first-test.md). For native XClock usage, see
[native integration](native-and-next.md). A matching wheel needs no local compilation;
building `uc-xcomm` from source requires system C++20 tools, CMake, and Python development headers.
