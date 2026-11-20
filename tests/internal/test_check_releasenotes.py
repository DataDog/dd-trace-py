"""Tests for ``scripts/check-releasenotes`` (the ``Ensure release note added`` step of the Changelog workflow)."""

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
    tmp_path: pathlib.Path, changed: list[str], title: str, labels: Optional[list[str]] = None
) -> subprocess.CompletedProcess:
    """Commit ``changed`` on top of a base commit and run the script as the PR check would."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
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
    "title", ["ci: tweak workflow", "ci(gitlab): x", "test(rq): bump", "docs(profiling): x", "docs!: x"]
)
def test_ci_test_docs_need_neither_note_nor_label(tmp_path, title):
    result = _run(tmp_path, ["ddtrace/x.py"], title)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ci_test_docs_may_still_add_a_note(tmp_path):
    result = _run(tmp_path, ["ddtrace/x.py", _NOTE], "ci: change published artifact")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("title", ["chore(x): y", "refactor(x): y", "perf(x): y", "fix(x): y", "feat(x): y"])
def test_other_types_without_note_or_label_are_rejected(tmp_path, title):
    result = _run(tmp_path, ["ddtrace/x.py"], title)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Release note not found" in result.stdout


@pytest.mark.parametrize("title", ["chore(x): y", "refactor(x): y", "perf(x): y"])
def test_opt_out_label_is_accepted(tmp_path, title):
    result = _run(tmp_path, ["ddtrace/x.py"], title, labels=["changelog/no-changelog"])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("title", ["chore(x): y", "refactor(x): y", "fix(x): y"])
def test_release_note_is_accepted(tmp_path, title):
    result = _run(tmp_path, ["ddtrace/x.py", _NOTE], title)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("title", ["fix(x): y", "feat(x): y"])
def test_fix_and_feat_reject_the_label(tmp_path, title):
    result = _run(tmp_path, ["ddtrace/x.py"], title, labels=["changelog/no-changelog"])
    assert result.returncode == 1, result.stdout + result.stderr
