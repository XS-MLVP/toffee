"""Choose release versions for merged PRs on main's first-parent history."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


RELEASE_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
BUMPS = ("patch", "minor", "major")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def release_bump(labels: list[dict]) -> str:
    selected = {label["name"] for label in labels} & {f"release:{part}" for part in BUMPS}
    if len(selected) != 1:
        raise ValueError(f"Expected exactly one release label; found {sorted(selected)}")
    return selected.pop().split(":", 1)[1]


def merged_pr(prs: list[dict], sha: str) -> dict | None:
    matches = [pr for pr in prs if pr.get("merged_at")
               and pr.get("base", {}).get("ref") == "main"
               and pr.get("merge_commit_sha") == sha]
    if len(matches) > 1:
        raise ValueError(f"Expected one merged main PR for {sha}; found {len(matches)}")
    return matches[0] if matches else None


def verify_commit(commit: str, base: str = "origin/main") -> str:
    sha = git("rev-parse", "--verify", f"{commit}^{{commit}}")
    if sha not in set(git("rev-list", "--first-parent", base).splitlines()):
        raise ValueError(f"{sha} is outside {base}'s first-parent history")
    repository = os.environ["GITHUB_REPOSITORY"]
    prs = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/commits/{sha}/pulls"], text=True))
    if merged_pr(prs, sha) is None:
        raise ValueError(f"{sha} is not the result of a merged main PR")
    return sha


def next_tag(bump: str, commit: str = "HEAD", base: str = "origin/main") -> str:
    if bump not in BUMPS:
        raise ValueError(f"Invalid version bump: {bump}")
    sha = git("rev-parse", "--verify", f"{commit}^{{commit}}")
    history = set(git("rev-list", "--first-parent", base).splitlines())
    if sha not in history:
        raise ValueError(f"{sha} is outside {base}'s first-parent history")
    releases = []
    for tag in git("tag", "--list", "v*").splitlines():
        match = RELEASE_TAG.fullmatch(tag)
        if match:
            target = git("rev-parse", f"refs/tags/{tag}^{{commit}}")
            if target in history:
                releases.append((tuple(map(int, match.groups())), tag, target))
    existing = [tag for _, tag, target in releases if target == sha]
    if len(existing) > 1:
        raise ValueError(f"Multiple release tags already point to {sha}: {existing}")
    if existing:
        return existing[0]  # Retry dispatch without creating another version.
    # Legacy tags belong to a separate history; the new Toffee line starts at 2.0.0.
    following = (2, 0, 0)
    if releases:
        current, previous, _ = max(releases)
        if subprocess.run(["git", "merge-base", "--is-ancestor", previous, sha]).returncode:
            raise ValueError(f"{sha} does not descend from latest release {previous}")
        major, minor, patch = current
        following = {"patch": (major, minor, patch + 1),
                     "minor": (major, minor + 1, 0), "major": (major + 1, 0, 0)}[bump]
    tag = "v" + ".".join(map(str, following))
    if tag in git("tag", "--list", tag).splitlines():
        raise ValueError(f"{tag} already exists outside the release history")
    return tag


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("label", "resolve", "verify", "next"))
    parser.add_argument("--json", type=Path)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--bump", choices=BUMPS)
    args = parser.parse_args()
    try:
        if args.command == "label":
            print(release_bump(json.loads(args.json.read_text())["pull_request"]["labels"]))
        elif args.command == "resolve":
            pr = merged_pr(json.loads(args.json.read_text()), args.commit)
            if pr is not None:
                print(release_bump(pr["labels"]))
        elif args.command == "verify":
            print(verify_commit(args.commit, args.base))
        else:
            print(next_tag(args.bump, args.commit, args.base))
    except ValueError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
