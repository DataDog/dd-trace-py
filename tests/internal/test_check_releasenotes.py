"""Tests for ``scripts/check-releasenotes``.

The script is the blocking ``Ensure release note added`` step of the Changelog workflow. These tests build
a throwaway git repository, replay the pull request shapes the script has to judge, and feed it a minimal
GitHub ``pull_request`` event payload, so they need neither network access nor a real checkout.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
from typing import Optional

import pytest


_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check-releasenotes"
_NOTE = "releasenotes/notes/some-note-0123456789abcdef.yaml"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("jq") is None,
    reason="scripts/check-releasenotes needs git and jq",
)


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _run(
    tmp_path: pathlib.Path,
    changed: list[str],
    title: str,
    labels: Optional[list[str]] = None,
) -> subprocess.CompletedProcess:
    """Commit ``changed`` on top of a base commit and run the script as the PR check would."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    # Do not inherit the developer's global signing / hook configuration.
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.hooksPath", "/dev/null")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    # The script diffs against ``origin/<base ref>``.
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    for name in changed:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "title": title,
                    "labels": [{"name": label} for label in labels or []],
                    "base": {"ref": "main"},
                }
            }
        )
    )
    return subprocess.run(
        ["bash", str(_SCRIPT_PATH)],
        cwd=repo,
        env={**os.environ, "GITHUB_EVENT_PATH": str(event)},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "changed",
    [
        ["tests/contrib/aiobotocore/test_x.py", "tests/requirements_locks/abc.txt", "supported_versions.json"],
        ["scripts/integration_registry/registry.yaml", "supported_versions.json"],
        ["riotfile.py", ".riot/requirements/abc123.txt"],
        ["scripts/some_script.py"],
    ],
)
def test_release_note_on_internal_only_change_is_rejected(tmp_path, changed):
    result = _run(tmp_path, changed + [_NOTE], "chore(x): test a new version")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "changelog/no-changelog" in result.stdout


@pytest.mark.parametrize("title", ["fix(x): something", "feat(x): something"])
def test_fix_and_feat_keep_their_release_note(tmp_path, title):
    # fix/feat PRs must carry a note regardless of which files they touch.
    result = _run(tmp_path, ["tests/contrib/x/test_x.py", _NOTE], title)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "changed",
    [
        ["ddtrace/contrib/internal/x/patch.py", "tests/contrib/x/test_x.py"],
        ["setup.py"],
        ["docs/index.rst"],
        # CI changes can alter the published artifacts, so they keep their note.
        [".github/workflows/build_deploy.yml"],
        [".gitlab/prepare-oci-package.sh"],
        [],
    ],
)
def test_release_note_with_a_shipped_change_is_accepted(tmp_path, changed):
    result = _run(tmp_path, changed + [_NOTE], "chore(x): something")
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_changelog_label_is_unchanged(tmp_path):
    result = _run(tmp_path, ["tests/contrib/x/test_x.py"], "chore(x): test", labels=["changelog/no-changelog"])
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_release_note_is_still_rejected(tmp_path):
    result = _run(tmp_path, ["ddtrace/contrib/internal/x/patch.py"], "chore(x): something")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Release note not found" in result.stdout
