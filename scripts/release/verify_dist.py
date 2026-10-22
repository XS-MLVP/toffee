"""Validate distribution metadata and create or verify a release manifest."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distributions(directory: Path, expected: str | None = None) -> tuple[str, list[Path]]:
    wheels, archives = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(archives) != 1:
        raise ValueError("Expected exactly one wheel and one source distribution")
    with zipfile.ZipFile(wheels[0]) as wheel:
        metadata = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Wheel must contain exactly one package metadata file")
        wheel_meta = BytesParser().parsebytes(wheel.read(metadata[0]))
        if "toffee/templates/unified_coverage.html" not in wheel.namelist():
            raise ValueError("Wheel is missing the coverage report template")
    with tarfile.open(archives[0]) as archive:
        metadata = [item for item in archive.getmembers()
                    if item.name.count("/") == 1 and item.name.endswith("/PKG-INFO")]
        if len(metadata) != 1 or not metadata[0].isfile():
            raise ValueError("Source distribution must contain one root PKG-INFO")
        stream = archive.extractfile(metadata[0])
        assert stream is not None
        source_meta = BytesParser().parsebytes(stream.read())
        if not any(name.endswith("/src/toffee/templates/unified_coverage.html")
                   for name in archive.getnames()):
            raise ValueError("Source distribution is missing the coverage report template")
    version = wheel_meta["Version"]
    if wheel_meta["Name"] != "pytoffee" or source_meta["Name"] != "pytoffee":
        raise ValueError("Distribution package name must be pytoffee")
    if not version or version != source_meta["Version"] or (expected and version != expected):
        raise ValueError(f"Package versions disagree: wheel={version}, "
                         f"sdist={source_meta['Version']}, expected={expected}")
    return version, wheels + archives


def verify_manifest(directory: Path, version: str, packages: list[Path], sha: str) -> None:
    manifest = json.loads((directory / "RELEASE-MANIFEST.json").read_text())
    expected_files = {path.name: digest(path) for path in packages}
    if manifest["version"] != version or manifest["git_sha"] != sha:
        raise ValueError("Manifest version or commit does not match the release checkout")
    if manifest["files"] != expected_files:
        raise ValueError("Package checksums disagree with the release manifest")
    dependencies = json.loads(Path(".github/native-dependencies.json").read_text())
    if manifest["native_dependencies"] != dependencies:
        raise ValueError("Manifest dependency pins do not match the tested checkout")
    if os.environ.get("GITHUB_RUN_ID") and manifest["ci_run_id"] != os.environ["GITHUB_RUN_ID"]:
        raise ValueError("Manifest belongs to a different CI run")
    files = packages + [directory / "RELEASE-MANIFEST.json"]
    sums = "".join(f"{digest(path)}  {path.name}\n" for path in sorted(files))
    if (directory / "SHA256SUMS").read_text() != sums:
        raise ValueError("SHA256SUMS does not describe the exact release files")
    if {path.name for path in directory.iterdir()} != {path.name for path in files} | {"SHA256SUMS"}:
        raise ValueError("Unexpected files in release bundle")


def verify_existing_assets(directory: Path, existing: Path) -> None:
    for path in existing.iterdir():
        expected = directory / path.name
        if not path.is_file() or not expected.is_file() or digest(path) != digest(expected):
            raise ValueError(f"Existing release asset differs from tested bundle: {path.name}")


def verify_existing_release(directory: Path, tag: str) -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    response = subprocess.run(["gh", "api", f"repos/{repository}/releases/tags/{tag}"],
                              capture_output=True, text=True)
    if response.returncode:
        if "(HTTP 404)" in response.stderr:
            return
        raise ValueError(f"Cannot inspect existing release: {response.stderr.strip()}")
    if not json.loads(response.stdout)["assets"]:
        return
    with tempfile.TemporaryDirectory(prefix="toffee-release-assets-") as temporary:
        subprocess.run(["gh", "release", "download", tag, "--repo", repository,
                        "--dir", temporary], check=True)
        verify_existing_assets(directory, Path(temporary))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--expected-version")
    parser.add_argument("--manifest", action="store_true")
    parser.add_argument("--verify-manifest", action="store_true")
    parser.add_argument("--existing-tag", help="reject previously published assets with different hashes")
    args = parser.parse_args()
    try:
        version, packages = distributions(args.dist, args.expected_version)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if args.manifest:
            manifest = {"version": version, "git_sha": sha,
                        "ci_run_id": os.environ.get("GITHUB_RUN_ID"),
                        "native_dependencies": json.loads(Path(".github/native-dependencies.json").read_text()),
                        "files": {path.name: digest(path) for path in packages}}
            path = args.dist / "RELEASE-MANIFEST.json"
            path.write_text(json.dumps(manifest, indent=2) + "\n")
            files = packages + [path]
            (args.dist / "SHA256SUMS").write_text(
                "".join(f"{digest(item)}  {item.name}\n" for item in sorted(files)))
        if args.verify_manifest:
            verify_manifest(args.dist, version, packages, sha)
        if args.existing_tag:
            verify_existing_release(args.dist, args.existing_tag)
        print(version)
    except ValueError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
