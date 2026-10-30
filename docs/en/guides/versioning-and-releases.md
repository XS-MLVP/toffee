# Versions, CI, and releases

Toffee derives versions from Git tags. Stable releases use `vX.Y.Z`; maintainers do not duplicate
version strings in source. For example, `v1.0.0` builds as `1.0.0`, and later untagged commits
build as development versions. `setuptools-scm` is a build dependency, not a requirement for using
an installed package.

## Current and Legacy versions

Toffee 2's `main` retains XReactor history. Its first merged-PR release is `v2.0.0`, distributed as
`uc-toffee==2.0.0`. Legacy `master` and `0.x` tags remain unchanged; XReactor's `1.x` tags stay in
its original repository. Legacy tags do not influence version calculation on the new `main`,
and the new workflow does not publish `pytoffee`. Later releases increment from `2.0.0` using
merged PR labels.

## Choose a version increment

A PR targeting `main` must select exactly one label:

| Label | Applicable change | Next version from 1.0.0 |
| --- | --- | --- |
| `release:patch` | Fixes, documentation, or maintenance preserving existing usage | 1.0.1 |
| `release:minor` | New public capability preserving existing calls | 1.1.0 |
| `release:major` | Removed interfaces or changed existing semantics | 2.0.0 |

Without a stable tag on main's first-parent history, the first version is fixed at `2.0.0`;
increments apply afterward. Every PR still needs one label. Missing or multiple increment labels
fail validation. Version workflows run against default branch `main`; `master` remains Legacy.

After merge, Version policy verifies that the target is the actual PR merge result on main's
first-parent history, then creates an annotated tag. Side-branch tags, internal PR commits, and
direct pushes do not trigger automatic releases. Retrying the same commit reuses its tag. If a
newer commit already owns the latest version, the workflow refuses to version an older commit.
Version creation is queued to prevent duplicate selections. GitHub's `queue: max` preserves
multiple pending runs during consecutive merges; see [concurrency queues](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).

## What CI validates

| Check | Acceptance criteria |
| --- | --- |
| Python 3.11 and 3.12 | Install and run the full regression; `--require-xspcomm` prevents native tests from silently skipping; run documented CLI examples |
| Native dependencies | Read full SHA and preview version from `.github/native-dependencies.json`, build xcomm, and check native ABI and coverage protocol separately |
| User documentation | `mkdocs build --strict` succeeds |
| Distributions | Wheel/sdist names, versions, and report templates agree; `twine check --strict` succeeds |
| Rebuild from sdist | Rebuild a wheel without Git metadata, preserving the same version |
| Isolated installation | Install wheel in a fresh venv; `pip check`, imports, and template access succeed |

Native tests use real XClock, but default CI does not build external RTL such as Cache or e203.
Follow each project's README for DUT builds, line coverage, and functional checks. Framework CI
success does not close DUT coverage; retain known DUT defect reproductions separately.

Pinned commits make verification reproducible; package versions define the user dependency contract.
When updating xcomm, change the pin, required capabilities, and relevant regressions together.
Picker generates actual DUT packages and is not a runtime dependency of the pure Python framework.
A generated DUT must still match its xcomm runtime.

## Which build is published

Tagged release accepts tag pushes, Version policy dispatches, or a tag selected manually by a
maintainer. It revalidates the tag's relationship to the merge result and invokes the same CI to
build and verify that commit. Only after checks pass does it publish a GitHub Release, uploading
the original CI artifacts without rebuilding in the publishing job. GitHub generates release
notes from merged PRs, so titles should describe observable changes.

Release artifacts include:

- One pure Python wheel and one source distribution.
- `RELEASE-MANIFEST.json`: version, source SHA, CI run ID, native pin, and each package's SHA-256.
- `SHA256SUMS`: checksums for distributions and the manifest.

Before publishing, the workflow rechecks the manifest, tag, dependencies, run ID, and file set.
Existing assets are not overwritten. Identical existing files permit adding missing assets;
checksum differences prevent mixed releases. After interruption, prefer rerunning only the failed
publishing job to reuse the original artifact. Following GitHub Release success, the same wheel
and sdist publish to PyPI's `uc-toffee`. Manifests and native test wheels remain CI/GitHub assets,
not uploads to that PyPI project. Ordinary CI also retains distributions for review. Coverage
databases, logs, and source-coverage HTML remain local.

Version policy invokes subsequent validation through `repository_dispatch`: tag pushes made with
`GITHUB_TOKEN` do not trigger other workflows, whereas dispatch supports this flow. See
[GitHub workflow triggers](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

## Configure PyPI

Publish a compatible `uc-xcomm` first, then `uc-toffee`. For initial setup, add a pending GitHub
publisher in [PyPI Publishing](https://pypi.org/manage/account/publishing/):

| Field | Value |
| --- | --- |
| Project name | `uc-toffee` |
| Owner | `XS-MLVP` |
| Repository | `toffee` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

Create the matching GitHub `pypi` environment and allow deployments from main or validated release
tags. No PyPI API token is needed. The first successful upload creates the project. If configuration
is incomplete, the PyPI job can fail and be retried after setup. An existing version cannot be
overwritten with different content.

`repository_dispatch` uses workflows on the repository default branch, which must remain `main`.
Legacy master's source and old pytoffee publishing setup remain independent.

## Initial enablement and recovery

An administrator first creates the three `release:*` labels and requires both Python jobs under
`CI / test`, plus `CI / docs`, `CI / package`, and `Version policy / release-label` for merging.
Enable Actions and allow the write permissions declared by version and publishing workflows.
Use the exact check names shown by the first Actions run.

Merge workflow files into the default branch before dispatch can find them. If the initial merge
does not start dispatch, or dispatch fails after tag creation, rerun Version policy on main with
the original full merge SHA and the same increment. It reuses an existing tag. If the tag exists
but validation or publication fails, run Tagged release manually with that tag. Manual runs still
require verified merge provenance and complete CI.

Before a local commit, run:

```bash
python3 -m pip install -e '.[test,docs]' 'build>=1,<2' 'twine>=6,<7'
python3 -m pytest -q --require-xspcomm
mkdocs build --strict
python3 -m build
python3 -m twine check --strict dist/*.whl dist/*.tar.gz
python3 scripts/release/verify_dist.py --manifest --verify-manifest
git diff --check
```

Read the installed version with `importlib.metadata.version("uc-toffee")`. Release CI needs full
Git history. An sdist includes its build-time version and rebuilds without the original repository.
Only plain source copies lacking both Git and sdist metadata use the `2.0.0.dev0` fallback.
See [setuptools-scm](https://setuptools-scm.readthedocs.io/en/v9.2.0/usage/) for configuration.
