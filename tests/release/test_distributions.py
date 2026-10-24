from __future__ import annotations

import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest

from scripts.release import verify_dist as distribution


@pytest.fixture
def packages(tmp_path: Path):
    def create(*, wheel_version="0.1.0", source_version="0.1.0", template=True):
        with zipfile.ZipFile(tmp_path / "uc_toffee.whl", "w") as wheel:
            wheel.writestr("uc_toffee.dist-info/METADATA",
                           f"Name: uc-toffee\nVersion: {wheel_version}\n")
            if template:
                wheel.writestr("toffee/templates/unified_coverage.html", "template")
        with tarfile.open(tmp_path / "uc_toffee.tar.gz", "w:gz") as archive:
            for name, contents in {
                "toffee/PKG-INFO": f"Name: uc-toffee\nVersion: {source_version}\n",
                "toffee/src/toffee/templates/unified_coverage.html": "template",
            }.items():
                data = contents.encode()
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        return tmp_path
    return create


def test_distributions_require_matching_metadata_and_expected_tag(packages):
    directory = packages()
    assert distribution.distributions(directory, "0.1.0")[0] == "0.1.0"
    with pytest.raises(ValueError, match="versions disagree"):
        distribution.distributions(directory, "0.2.0")
    packages(source_version="0.2.0")
    with pytest.raises(ValueError, match="versions disagree"):
        distribution.distributions(directory)


def test_missing_template_and_multiple_packages_are_rejected(packages):
    directory = packages(template=False)
    with pytest.raises(ValueError, match="template"):
        distribution.distributions(directory)
    packages()
    (directory / "extra.whl").write_bytes(b"extra")
    with pytest.raises(ValueError, match="exactly one wheel"):
        distribution.distributions(directory)


def test_existing_release_can_only_be_completed_with_identical_assets(packages, tmp_path):
    directory = packages()
    existing = tmp_path / "published"
    existing.mkdir()
    original = directory / "uc_toffee.whl"
    (existing / original.name).write_bytes(original.read_bytes())
    distribution.verify_existing_assets(directory, existing)
    (existing / original.name).write_bytes(b"different build")
    with pytest.raises(ValueError, match="differs"):
        distribution.verify_existing_assets(directory, existing)


@pytest.mark.parametrize("damage", ["commit", "version", "dependency", "run", "checksum", "extra"])
def test_publish_rejects_mismatched_or_tampered_bundles(packages, monkeypatch, damage):
    directory = packages()
    version, files = distribution.distributions(directory)
    sha = "a" * 40
    deps = json.loads(Path(".github/native-dependencies.json").read_text())
    manifest = {"version": version, "git_sha": sha, "ci_run_id": "123",
                "native_dependencies": deps,
                "files": {path.name: distribution.digest(path) for path in files}}
    path = directory / "RELEASE-MANIFEST.json"
    path.write_text(json.dumps(manifest))
    (directory / "SHA256SUMS").write_text("".join(
        f"{distribution.digest(item)}  {item.name}\n" for item in sorted(files + [path])))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    distribution.verify_manifest(directory, version, files, sha)
    if damage == "commit":
        sha = "b" * 40
    elif damage == "version":
        version = "0.2.0"
    elif damage == "dependency":
        manifest["native_dependencies"] = {}
        path.write_text(json.dumps(manifest))
    elif damage == "run":
        monkeypatch.setenv("GITHUB_RUN_ID", "456")
    elif damage == "checksum":
        files[0].write_bytes(b"corrupt")
    else:
        (directory / "unexpected.txt").write_text("extra")
    with pytest.raises(ValueError):
        distribution.verify_manifest(directory, version, files, sha)
