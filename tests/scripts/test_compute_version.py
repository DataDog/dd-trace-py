#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "packaging==23.1",
#   "pytest==8.3.3",
# ]
# ///
"""Tests for scripts/compute_version.py, against a synthetic git repo - never this repo's real
history, so these stay hermetic and fast.
"""

from pathlib import Path
import re
import subprocess
import sys

from packaging.version import Version
import pytest


sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from compute_version import compute_from_git
from compute_version import local_git_branch
from compute_version import resolve_branch_name


# The exact shape system-tests/utils/_context/component_version.py expects for Python: a bare
# `X.Y.Z`, `X.Y.ZrcN`, or `X.Y.Z.devN` - exactly one non-numeric suffix token, never combined.
SYSTEM_TESTS_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+((rc\d+)|(\.dev\d+))?$")


class GitRepo:
    """Minimal helper for building a throwaway git repo with fabricated commits/tags/branches."""

    def __init__(self, path: Path):
        self.path = path
        self._counter = 0
        self._run("init", "-q")
        self._run("config", "user.email", "test@example.com")
        self._run("config", "user.name", "Test")
        # This is a throwaway repo; disable signing regardless of the machine's global git config,
        # so tests never block on a GPG/SSH-key prompt.
        self._run("config", "commit.gpgsign", "false")
        self._run("config", "tag.gpgsign", "false")
        self._run("checkout", "-q", "-b", "main")

    def _run(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.path, capture_output=True, text=True)
        assert result.returncode == 0, f"git {args} failed: {result.stderr}"
        return result.stdout.strip()

    def commit(self) -> str:
        self._counter += 1
        (self.path / f"file{self._counter}.txt").write_text(str(self._counter))
        self._run("add", "-A")
        self._run("commit", "-q", "-m", f"commit {self._counter}")
        return self.head()

    def head(self) -> str:
        return self._run("rev-parse", "HEAD")

    def tag(self, name: str) -> None:
        self._run("tag", name)

    def branch(self, name: str, start: str = "main") -> None:
        self._run("checkout", "-q", "-b", name, start)

    def checkout(self, ref: str) -> None:
        self._run("checkout", "-q", ref)

    def dirty(self) -> None:
        (self.path / "uncommitted.txt").write_text("dirty")


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    return GitRepo(tmp_path)


def test_exact_tag_build(repo: GitRepo) -> None:
    repo.commit()
    repo.tag("v4.13.0")
    assert compute_from_git(repo.path, {}) == "4.13.0"


def test_exact_rc_tag_build(repo: GitRepo) -> None:
    repo.commit()
    repo.tag("v4.13.0rc2")
    assert compute_from_git(repo.path, {}) == "4.13.0rc2"


def test_release_branch_with_existing_patch_tag(repo: GitRepo) -> None:
    repo.commit()
    repo.branch("4.13")
    repo.commit()
    repo.tag("v4.13.0")
    repo.commit()
    repo.commit()
    repo.commit()
    assert compute_from_git(repo.path, {}) == "4.13.1.dev3"


def test_release_branch_freshly_cut_no_tag_yet(repo: GitRepo) -> None:
    repo.commit()  # fork point
    repo.branch("4.14")
    repo.commit()
    repo.commit()
    assert compute_from_git(repo.path, {}) == "4.14.0.dev2"


def test_main_right_after_a_minor_release(repo: GitRepo) -> None:
    repo.commit()
    repo.tag("v4.4.0")
    repo.commit()
    repo.commit()
    assert compute_from_git(repo.path, {}) == "4.5.0.dev2"


def test_main_right_after_a_patch_only_release_on_a_sibling_branch(repo: GitRepo) -> None:
    """The single most important regression test: release branches never merge back into main, so
    `v4.12.3` here is NOT an ancestor of main's HEAD. Ancestry-based tag lookup (what broke
    setuptools_scm's `release-branch-semver` scheme in a related way) would miss this tag
    entirely; the correct behavior comes from a global, ancestry-independent version comparison.
    """
    repo.commit()  # fork point, shared ancestor
    repo.branch("4.12")
    repo.commit()
    repo.tag("v4.12.3")  # final release lives only on the sibling branch
    repo.checkout("main")
    repo.commit()
    repo.commit()  # 2 commits on main since the fork, never touching 4.12's history

    assert "v4.12.3" not in repo._run("tag", "--merged", "HEAD")
    assert compute_from_git(repo.path, {}) == "4.13.0.dev2"


def test_detached_head_needs_override_to_be_correctly_classified(repo: GitRepo) -> None:
    """Demonstrates exactly the failure mode that broke setuptools_scm here: under a detached
    HEAD (the norm for CI checkouts) with no override, a release branch is silently treated as
    "not a release branch" - the override env var is what fixes that.
    """
    repo.commit()  # fork point
    repo.branch("4.15")
    repo.commit()
    repo.commit()
    sha = repo.head()
    repo.checkout(sha)  # detach

    assert local_git_branch(repo.path) is None

    # Without the override: silently misclassified as a non-release branch.
    without_override = compute_from_git(repo.path, {})
    assert not without_override.startswith("4.15.")

    # With the override: correctly classified as the 4.15 release branch.
    with_override = compute_from_git(repo.path, {"_DD_TRACE_BUILD_VERSION": "4.15"})
    assert with_override == "4.15.0.dev2"


def test_dirty_worktree_has_no_effect(repo: GitRepo) -> None:
    repo.commit()
    repo.branch("4.16")
    repo.commit()
    clean_version = compute_from_git(repo.path, {})

    repo.dirty()
    dirty_version = compute_from_git(repo.path, {})

    assert dirty_version == clean_version
    assert "+" not in dirty_version


def test_github_pull_request_prefers_head_ref_over_ref_name(repo: GitRepo) -> None:
    repo.commit()  # fork point
    repo.branch("4.17")
    repo.commit()
    repo.commit()
    repo.checkout(repo.head())  # detach, as a GH Actions PR checkout of a specific SHA would be

    env = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_HEAD_REF": "4.17",
        "GITHUB_REF_NAME": "99/merge",  # must NOT be used for pull_request events
    }
    assert compute_from_git(repo.path, env) == "4.17.0.dev2"


def test_gitlab_merge_queue_ref_resolves_to_release_branch(repo: GitRepo) -> None:
    repo.commit()  # fork point
    repo.branch("4.18")
    repo.commit()
    repo.checkout(repo.head())  # detach

    env = {"CI_COMMIT_REF_NAME": "mq-working-branch-4.18-abc1234"}
    assert compute_from_git(repo.path, env) == "4.18.0.dev1"


def test_resolve_branch_name_priority_chain() -> None:
    # Manual override always wins.
    assert resolve_branch_name({"_DD_TRACE_BUILD_VERSION": "4.13", "CI_COMMIT_BRANCH": "main"}) == "4.13"
    # GitHub PR: GITHUB_HEAD_REF, never GITHUB_REF_NAME (which is "<pr>/merge" on that event).
    assert (
        resolve_branch_name(
            {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_HEAD_REF": "4.13", "GITHUB_REF_NAME": "42/merge"}
        )
        == "4.13"
    )
    # GitHub push: GITHUB_REF_NAME is the real branch name.
    assert resolve_branch_name({"GITHUB_EVENT_NAME": "push", "GITHUB_REF_NAME": "main"}) == "main"
    # GitLab branch pipeline.
    assert resolve_branch_name({"CI_COMMIT_BRANCH": "4.13"}) == "4.13"
    # GitLab merge-queue ref, unwrapped.
    assert resolve_branch_name({"CI_COMMIT_REF_NAME": "mq-working-branch-4.13-abc1234"}) == "4.13"
    # Nothing set: unresolved.
    assert resolve_branch_name({}) is None


@pytest.mark.parametrize(
    "version",
    [
        "4.13.0",
        "4.13.0rc2",
        "4.13.1.dev3",
        "4.14.0.dev2",
        "4.5.0.dev2",
        "4.13.0.dev0",
    ],
)
def test_output_matches_system_tests_version_contract(version: str) -> None:
    assert SYSTEM_TESTS_VERSION_RE.match(version), version
    # Also round-trips through packaging.version.Version without normalizing away, matching
    # scripts/verify-package-version's PEP 440 strictness check.
    assert str(Version(version)) == version


if __name__ == "__main__":
    # --confcutdir stops pytest from climbing up to the repo-root conftest.py, which pulls in
    # test-suite-only dependencies (e.g. hypothesis) this standalone script doesn't declare.
    # -o addopts="" overrides setup.cfg's [tool:pytest] addopts (coverage flags this standalone
    # script's ephemeral environment doesn't have the plugins for).
    sys.exit(pytest.main([__file__, "-v", f"--confcutdir={Path(__file__).parent}", "-o", "addopts="]))
