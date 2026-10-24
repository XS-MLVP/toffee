from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts.release import version_policy as policy


def pr(sha: str, labels=("release:minor",), *, base="main", merged=True):
    return {"merge_commit_sha": sha, "base": {"ref": base},
            "merged_at": "2026-10-07" if merged else None,
            "labels": [{"name": label} for label in labels]}


@pytest.fixture
def repository(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], check=True)
    policy.git("config", "user.name", "Release test")
    policy.git("config", "user.email", "release@example.invalid")

    def commit(value="initial"):
        (tmp_path / "content").write_text(value)
        policy.git("add", "content")
        policy.git("commit", "-qm", value)
        return policy.git("rev-parse", "HEAD")

    commit()
    return commit


@pytest.mark.parametrize("labels", [(), ("bug",), ("release:minor", "release:patch")])
def test_requires_exactly_one_release_label(labels):
    with pytest.raises(ValueError, match="exactly one"):
        policy.release_bump([{"name": label} for label in labels])


def test_resolves_only_the_matching_main_merge():
    matching = pr("target", ("bug", "release:patch"))
    assert policy.merged_pr([pr("other"), pr("target", base="topic"),
                             pr("target", merged=False), matching], "target") is matching
    assert policy.release_bump(matching["labels"]) == "patch"
    assert policy.merged_pr([matching], "direct-push") is None
    with pytest.raises(ValueError, match="one merged"):
        policy.merged_pr([matching, matching], "target")


@pytest.mark.parametrize("bump,expected", [("patch", "v0.2.4"), ("minor", "v0.3.0"),
                                          ("major", "v1.0.0")])
def test_bumps_from_the_latest_release(repository, bump, expected):
    policy.git("tag", "v0.2.3")
    repository("next")
    assert policy.next_tag(bump, base="main") == expected


@pytest.mark.parametrize("bump", policy.BUMPS)
def test_first_release_and_retry_do_not_create_extra_versions(repository, bump):
    assert policy.next_tag(bump, base="main") == "v2.0.0"
    policy.git("tag", "-a", "v2.0.0", "-m", "first release")
    assert policy.next_tag(bump, base="main") == "v2.0.0"


@pytest.mark.parametrize("bump,expected", [("patch", "v2.0.1"), ("minor", "v2.1.0"),
                                          ("major", "v3.0.0")])
def test_legacy_history_does_not_determine_new_line_versions(repository, bump, expected):
    policy.git("checkout", "-q", "--orphan", "master")
    repository("legacy")
    policy.git("tag", "v0.1.0")
    policy.git("tag", "v0.3.1")
    legacy = policy.git("rev-parse", "HEAD")
    policy.git("checkout", "-q", "main")
    assert policy.next_tag(bump, base="main") == "v2.0.0"
    policy.git("tag", "-a", "v2.0.0", "-m", "first release")
    repository("next release")
    assert policy.next_tag(bump, base="main") == expected
    assert policy.git("rev-parse", "master", "v0.1.0", "v0.3.1").splitlines() == [legacy] * 3


def test_side_branch_tags_are_ignored_and_cannot_be_released(repository):
    main_sha = policy.git("rev-parse", "HEAD")
    policy.git("checkout", "-qb", "topic")
    side = repository("side")
    policy.git("tag", "v99.0.0")
    policy.git("checkout", "-q", "main")
    assert policy.next_tag("minor", base="main") == "v2.0.0"
    with pytest.raises(ValueError, match="first-parent"):
        policy.next_tag("minor", side, "main")
    assert policy.git("rev-parse", "HEAD") == main_sha


def test_rejects_a_conflicting_tag_and_an_out_of_order_commit(repository):
    original = policy.git("rev-parse", "HEAD")
    policy.git("checkout", "-qb", "topic")
    repository("side")
    policy.git("tag", "v2.0.0")
    policy.git("checkout", "-q", "main")
    with pytest.raises(ValueError, match="already exists"):
        policy.next_tag("minor", base="main")
    repository("new release")
    policy.git("tag", "v1.0.0")
    with pytest.raises(ValueError, match="does not descend"):
        policy.next_tag("patch", original, "main")


def test_merge_provenance_requires_a_real_associated_merge(repository, monkeypatch):
    sha = policy.git("rev-parse", "HEAD")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/toffee")
    check_output = subprocess.check_output
    responses = []

    def fetch(args, **kwargs):
        if args[0] == "gh":
            assert args == ["gh", "api", f"repos/example/toffee/commits/{sha}/pulls"]
            return json.dumps(responses)
        return check_output(args, **kwargs)

    monkeypatch.setattr(subprocess, "check_output", fetch)
    with pytest.raises(ValueError, match="not the result"):
        policy.verify_commit("HEAD", "main")
    responses.append(pr(sha))
    assert policy.verify_commit("HEAD", "main") == sha
