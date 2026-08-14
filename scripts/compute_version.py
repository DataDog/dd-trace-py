#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "packaging==23.1",
# ]
# ///
"""Compute ddtrace's version dynamically from git state, without setuptools_scm.

Prints the resolved version to stdout. Invoked by `setup.py` on every build (sdist, wheel, PEP 517
hook) and directly by CI to populate downstream jobs.

Rules, in order:

1. HEAD is exactly a release tag (`vX.Y.Z[rcN]`) -> that version, verbatim. rc/final versions are
   only ever produced by a human creating that exact tag - never guessed.
2. Otherwise, resolve the branch this build is for via `resolve_branch_name` (explicit CI-provided
   ref info, never `git rev-parse --abbrev-ref HEAD` alone - that returns the literal string
   "HEAD" under a detached checkout, which is the norm for CI, and is exactly the failure mode that
   broke setuptools_scm's `release-branch-semver` scheme here for years: see
   docs/contributing-release.rst and the git history of `pyproject.toml`'s `version_scheme` for
   the long version).
   - If the branch matches dd-trace-py's release-branch shape (`X.Y`, e.g. "4.13"): the next patch
     of the latest matching tag reachable by ancestry on this branch (release branches' own tags
     are always their ancestors, since branches don't merge back but do extend forward from their
     own tags), or `X.Y.0` if the branch was just cut and has no tag yet. Suffixed `.devN`.
   - Otherwise (main, feature branches, ...): the next minor after the globally latest *final* tag
     in the whole repo, found by version-number comparison rather than ancestry. Ancestry doesn't
     work here because release branches never merge back into main, so a release branch's tags are
     never reachable from main - this is the same reason `scripts/resolve_previous_version.py`
     (which this module reuses tag-parsing from) had to abandon ancestry-based lookups. Suffixed
     `.devN`.

No PEP 440 local segments (`+dirty`, `+gHASH`): not part of the version shape
`system-tests/utils/_context/component_version.py` expects, and not needed here.
"""

import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import typing as t


sys.path.insert(0, str(Path(__file__).parent))

from version_lib import BRANCH_RE
from version_lib import latest_final_release
from version_lib import parse_tag


FROZEN_VERSION_PATH = "ddtrace/_version_frozen.py"

_MQ_BRANCH_RE = re.compile(r"^mq-working-branch-(.+)-[0-9a-f]{7,40}$")


def run_git(*args: str, cwd: Path) -> str:
    """Run a git command in `cwd`, returning stripped stdout, raising on non-zero exit."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result.stdout.strip()


def git_available(repo_root: Path) -> bool:
    """Whether `repo_root` is a usable git working tree (git binary present, real repo)."""
    try:
        run_git("rev-parse", "--is-inside-work-tree", cwd=repo_root)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False


def exact_tag_version(repo_root: Path) -> t.Optional[str]:
    """The version HEAD is exactly tagged as (`v` stripped), or None if HEAD isn't a tag."""
    try:
        tag = run_git("describe", "--tags", "--exact-match", "--match", "v*", "HEAD", cwd=repo_root)
    except subprocess.CalledProcessError:
        return None
    return tag[1:] if tag.startswith("v") else tag


def resolve_branch_name(env: t.Mapping[str, str]) -> t.Optional[str]:
    """Resolve the branch this build is for from explicit CI-provided ref info.

    Deliberately never falls back to `git rev-parse --abbrev-ref HEAD` here (see
    `local_git_branch` for that, kept separate so this stays a pure, git-free function) - that
    command returns the literal string "HEAD" under a detached checkout, which is what CI
    checkouts normally are, and silently misclassifying a release branch as "not a release branch"
    is exactly the bug that made setuptools_scm's `release-branch-semver` scheme unusable here.

    >>> resolve_branch_name({"_DD_TRACE_BUILD_VERSION": "4.13"})
    '4.13'
    >>> env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_HEAD_REF": "4.13", "GITHUB_REF_NAME": "42/merge"}
    >>> resolve_branch_name(env)
    '4.13'
    >>> resolve_branch_name({"GITHUB_EVENT_NAME": "push", "GITHUB_REF_NAME": "main"})
    'main'
    >>> resolve_branch_name({"CI_COMMIT_BRANCH": "4.13"})
    '4.13'
    >>> resolve_branch_name({"CI_COMMIT_REF_NAME": "mq-working-branch-4.13-abc1234"})
    '4.13'
    >>> resolve_branch_name({"CI_COMMIT_REF_NAME": "main"})
    'main'
    >>> resolve_branch_name({}) is None
    True
    """
    override = env.get("_DD_TRACE_BUILD_VERSION")
    if override:
        return override

    if env.get("GITHUB_EVENT_NAME") == "pull_request":
        head_ref = env.get("GITHUB_HEAD_REF")
        if head_ref:
            return head_ref
    elif env.get("GITHUB_EVENT_NAME") in ("push", "workflow_dispatch"):
        ref_name = env.get("GITHUB_REF_NAME")
        if ref_name:
            return ref_name

    branch = env.get("CI_COMMIT_BRANCH")
    if branch:
        return branch

    ref_name = env.get("CI_COMMIT_REF_NAME")
    if ref_name:
        mq_match = _MQ_BRANCH_RE.match(ref_name)
        return mq_match.group(1) if mq_match else ref_name

    return None


def local_git_branch(repo_root: Path) -> t.Optional[str]:
    """Best-effort local-dev branch fallback; None under a detached HEAD (mirrors CI's default)."""
    try:
        branch = run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return None if branch == "HEAD" else branch


def is_release_branch(name: str) -> t.Optional[tuple[int, int]]:
    """(major, minor) if `name` has dd-trace-py's release-branch shape (`X.Y`), else None.

    >>> is_release_branch("4.13")
    (4, 13)
    >>> is_release_branch("main") is None
    True
    >>> is_release_branch("4.13.0") is None
    True
    """
    match = BRANCH_RE.match(name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _commit_count_since(repo_root: Path, ref: str) -> int:
    return int(run_git("rev-list", "--count", f"{ref}..HEAD", cwd=repo_root))


def _fork_point_distance(repo_root: Path) -> int:
    """Commits since this branch forked from `main`, or total commit count if that's unknowable."""
    for main_ref in ("main", "origin/main"):
        try:
            base = run_git("merge-base", "HEAD", main_ref, cwd=repo_root)
        except subprocess.CalledProcessError:
            continue
        return _commit_count_since(repo_root, base)
    try:
        return int(run_git("rev-list", "--count", "HEAD", cwd=repo_root))
    except subprocess.CalledProcessError:
        return 0


def release_branch_version(repo_root: Path, major: int, minor: int) -> str:
    """Next patch dev-version for release branch `major.minor` (rule 3)."""
    raw_tags = run_git("tag", "--list", f"v{major}.{minor}.*", "--merged", "HEAD", cwd=repo_root)
    tags = [t for t in raw_tags.splitlines() if t]
    tag = latest_final_release(tags)
    if tag is not None:
        version = parse_tag(tag)
        assert version is not None  # tag came from latest_final_release, always parses
        patch = version.micro + 1
        dev_count = _commit_count_since(repo_root, tag)
    else:
        patch = 0
        dev_count = _fork_point_distance(repo_root)
    return f"{major}.{minor}.{patch}.dev{dev_count}"


def main_or_other_branch_version(repo_root: Path) -> str:
    """Next minor dev-version off the globally latest final release tag (rule 4)."""
    raw_tags = run_git("tag", "-l", cwd=repo_root)
    tags = [t for t in raw_tags.splitlines() if t]
    tag = latest_final_release(tags)
    if tag is None:
        total = int(run_git("rev-list", "--count", "HEAD", cwd=repo_root))
        return f"0.1.0.dev{total}"
    version = parse_tag(tag)
    assert version is not None  # tag came from latest_final_release, always parses
    dev_count = _commit_count_since(repo_root, tag)
    return f"{version.major}.{version.minor + 1}.0.dev{dev_count}"


def compute_from_git(repo_root: Path, env: t.Mapping[str, str]) -> str:
    """The full version-decision state machine, given a git repo and a CI-like environment."""
    exact = exact_tag_version(repo_root)
    if exact is not None:
        return exact

    branch = resolve_branch_name(env) or local_git_branch(repo_root)
    if branch is not None:
        release_branch = is_release_branch(branch)
        if release_branch is not None:
            return release_branch_version(repo_root, *release_branch)

    return main_or_other_branch_version(repo_root)


def read_frozen_version(repo_root: Path) -> t.Optional[str]:
    """The version frozen into `ddtrace/_version_frozen.py` at sdist-build time, if present."""
    path = repo_root / FROZEN_VERSION_PATH
    if not path.exists():
        return None
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, OSError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "version":
                try:
                    value = ast.literal_eval(node.value)
                except ValueError:
                    return None
                return value if isinstance(value, str) else None
    return None


def write_frozen_version(repo_root: Path, version: str) -> None:
    """Freeze `version` into `ddtrace/_version_frozen.py`, so a later wheel-from-sdist build (no
    git available there) can read it back deterministically. Best-effort: a read-only source tree
    shouldn't fail the build over this.
    """
    path = repo_root / FROZEN_VERSION_PATH
    try:
        path.write_text(
            f'# Auto-generated by scripts/compute_version.py - do not edit by hand.\nversion = "{version}"\n'
        )
    except OSError as exc:
        print(f"warning: could not write {path}: {exc}", file=sys.stderr)


def resolve_version(repo_root: Path) -> str:
    """The public entry point: compute fresh from git, or fall back to the frozen value.

    Raises if neither is available - that means a genuinely broken packaging environment (no git
    *and* no frozen version file), which should fail the build loudly rather than ship a silently
    wrong placeholder version.
    """
    if git_available(repo_root):
        version = compute_from_git(repo_root, os.environ)
        write_frozen_version(repo_root, version)
        return version

    frozen = read_frozen_version(repo_root)
    if frozen is not None:
        return frozen

    raise RuntimeError(
        f"Cannot resolve the ddtrace version: {repo_root} is not a git repository and "
        f"{FROZEN_VERSION_PATH} is missing or unreadable. This is a broken packaging environment."
    )


if __name__ == "__main__":
    print(resolve_version(Path.cwd()))
