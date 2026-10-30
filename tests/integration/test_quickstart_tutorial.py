"""Keep the walkthrough excerpts and intentional failure commands usable."""

from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples/getting_started"
BLOCK = re.compile(
    r"<!-- executable-example: ([^\s#]+)(?:#([a-z_]+))? -->\n```python\n(.*?)\n```",
    re.DOTALL,
)


@pytest.mark.parametrize("language", ("zh", "en"))
def test_tutorial_excerpts_match_the_executed_source(language):
    included = set()
    regions = set()
    for page in (ROOT / "docs" / language / "getting-started").glob("*.md"):
        for name, region, code in BLOCK.findall(page.read_text()):
            path = ROOT / name
            assert path.is_relative_to(EXAMPLES), (page, path)
            source = path.read_text()
            if region:
                source = source.split(f"# region {region}\n", 1)[1]
                source = source.split("\n# endregion", 1)[0]
                regions.add((path.name, region))
            assert code.strip() == source.strip(), (page, path, region)
            included.add(path.name)
    assert {p.name for p in EXAMPLES.glob("test_*.py")} <= included
    assert {"failure_lab.py", "native_lab.py"} <= included
    main = EXAMPLES / "test_accumulator.py"
    assert {
        (main.name, name)
        for name in re.findall(r"^# region ([a-z_]+)$", main.read_text(), re.MULTILINE)
    } <= regions


@pytest.mark.parametrize("fault, exception", [
    ("wrong", "ScoreboardMismatch"),
    ("missing", "ScoreboardTimeoutError"),
    ("late", "ScoreboardTimeoutError"),
    ("extra", "ScoreboardAssociationError"),
])
def test_failure_lab_reports_the_documented_failure(fault, exception):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(EXAMPLES / "failure_lab.py"),
         "--tb=short", "-k", fault],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert exception in result.stdout
    assert "1 failed, 3 deselected" in result.stdout
