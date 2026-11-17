"""The CI-built native package must satisfy Toffee's published dependency range."""

import json
from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[2]


def test_ci_native_version_satisfies_the_package_requirement():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    requirement = next(Requirement(item) for item in project["project"]["dependencies"]
                       if Requirement(item).name == "uc-xcomm")
    native = json.loads((ROOT / ".github/native-dependencies.json").read_text())["xcomm"]
    assert Version(native["package_version"]) in requirement.specifier
