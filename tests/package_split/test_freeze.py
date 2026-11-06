"""freeze gate: protected source paths against the extraction baseline.

Each test builds a throwaway git repository, pins an extraction revision,
commits a freeze manifest at the base revision, and then checks that the
gate rejects unauthorized edits, additions and expired exceptions while
tolerating approved rewrites.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

from package_split.evidence import FAIL
from package_split.evidence import NOT_RUN
from package_split.evidence import PASS
from package_split.gates import freeze

from ._support import Args
from ._support import make_report
from ._support import make_tree


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, "git %s failed: %s" % (" ".join(args), result.stderr)
    return result.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


FREEZE_TEMPLATE = """\
schema_version = 1

[[frozen]]
destination_repository = "datadog-apm-python-test"
extraction_revision = "{extraction_revision}"
owner = "test-owner"
source_paths = ["sample"]
build_inputs = ["pyproject.toml"]

[frozen.hashes]
"sample/__init__.py" = "{init_hash}"
"sample/mod.py" = "{mod_hash}"

{rewrites}{exceptions}
"""

REWRITE = """
[[frozen.approved_rewrites]]
path = "sample/mod.py"
reason = "compatibility shim"
approver = "test-owner"
sync_record = "recorded"
"""

EXCEPTION = """
[[freeze_exceptions]]
path = "sample/"
reason = "pending"
owner = "test-owner"
expiry = "{expiry}"
sync_record = "recorded"
"""


class FreezeRepo:
    def __init__(self, root: Path, rewrites: str = "", exceptions: str = "") -> None:
        self.root = root
        make_tree(
            root,
            "sample",
            {"__init__.py": "__version__ = '1.0'\n", "mod.py": "value = 1\n"},
        )
        git(root, "init", ".")
        git(root, "config", "user.email", "test@example.com")
        git(root, "config", "user.name", "package-split tests")
        # Point the unborn HEAD at "main" regardless of git's default.
        subprocess.run(
            ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        git(root, "add", "-A")
        git(root, "commit", "-m", "extraction")
        self.extraction_revision = git(root, "rev-parse", "HEAD")

        freeze_text = FREEZE_TEMPLATE.format(
            extraction_revision=self.extraction_revision,
            init_hash=sha256(root / "sample" / "__init__.py"),
            mod_hash=sha256(root / "sample" / "mod.py"),
            rewrites=rewrites,
            exceptions=exceptions,
        )
        (root / "package-freeze.toml").write_text(freeze_text, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-m", "freeze")

    def run(self, base: str = "main", head: str | None = None):
        from package_split.manifest import load_manifest

        manifest = load_manifest(self.root / "package-contract.toml")
        report = make_report("freeze", self.root / "package-contract.toml")
        freeze.run_gate(manifest, Args(base=base, head=head), report)
        return report

    def commit_change(self, message: str) -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", message)


def test_no_drift_passes(tmp_path):
    repo = FreezeRepo(tmp_path)
    report = repo.run()
    assert report.status == PASS, report.summary()
    assert any(c.name == "source-freeze" and c.status == PASS for c in report.checks)


def test_unauthorized_edit_fails(tmp_path):
    repo = FreezeRepo(tmp_path)
    (repo.root / "sample" / "mod.py").write_text("value = 2\n", encoding="utf-8")
    repo.commit_change("unauthorized edit")
    report = repo.run()
    assert report.status == FAIL
    source_freeze = [c for c in report.checks if c.name == "source-freeze"]
    assert source_freeze and source_freeze[0].status == FAIL
    assert any("unauthorized edit" in p for p in source_freeze[0].data["problems"])


def test_unauthorized_addition_fails(tmp_path):
    repo = FreezeRepo(tmp_path)
    (repo.root / "sample" / "sneaky.py").write_text("x = 1\n", encoding="utf-8")
    repo.commit_change("unauthorized addition")
    report = repo.run()
    assert report.status == FAIL
    assert any(
        "unauthorized addition" in p
        for c in report.checks
        if c.name == "source-freeze"
        for p in c.data.get("problems", [])
    )


def test_unauthorized_deletion_fails(tmp_path):
    repo = FreezeRepo(tmp_path)
    (repo.root / "sample" / "mod.py").unlink()
    repo.commit_change("unauthorized deletion")
    report = repo.run()
    assert report.status == FAIL


def test_head_option_measures_pr_drift_not_base(tmp_path):
    # A PR-shaped run: the freeze rules live at the base revision, while the
    # drift is measured against a different head (the PR). The workflow in
    # .github/workflows/package-split-freeze-guard.yml relies on this.
    repo = FreezeRepo(tmp_path)
    # Simulate the PR: create a branch with an unauthorized edit and move
    # the default branch back to the freeze commit.
    git(repo.root, "checkout", "-b", "pr-head")
    (repo.root / "sample" / "mod.py").write_text("value = 2\n", encoding="utf-8")
    repo.commit_change("unauthorized edit on the PR branch")
    git(repo.root, "checkout", "main")

    report = repo.run(base="main", head="pr-head")
    assert report.status == FAIL
    source_freeze = [c for c in report.checks if c.name == "source-freeze"]
    assert source_freeze and source_freeze[0].status == FAIL
    assert any("unauthorized edit" in p for p in source_freeze[0].data["problems"])


def test_approved_rewrite_tolerated(tmp_path):
    repo = FreezeRepo(tmp_path, rewrites=REWRITE)
    (repo.root / "sample" / "mod.py").write_text("value = 2\n", encoding="utf-8")
    repo.commit_change("approved rewrite")
    report = repo.run()
    assert report.status == PASS, report.summary()
    assert any(c.name == "approved-rewrites" for c in report.checks)


def test_expired_exception_fails(tmp_path):
    repo = FreezeRepo(tmp_path, exceptions=EXCEPTION.format(expiry="2001-01-01"))
    report = repo.run()
    assert report.status == FAIL
    assert any("expired" in str(c.data.get("problems", [])) for c in report.checks)


def test_recorded_hash_mismatch_fails(tmp_path):
    repo = FreezeRepo(tmp_path)
    # Rewrite the freeze manifest at the base revision itself with a bad
    # hash: the manifest integrity check must catch it.
    bad = FREEZE_TEMPLATE.format(
        extraction_revision=repo.extraction_revision,
        init_hash="0" * 64,
        mod_hash=sha256(repo.root / "sample" / "mod.py"),
        rewrites="",
        exceptions="",
    )
    (repo.root / "package-freeze.toml").write_text(bad, encoding="utf-8")
    repo.commit_change("tampered freeze manifest")
    report = repo.run(base="HEAD")
    assert report.status == FAIL


def test_missing_freeze_manifest_is_not_run(tmp_path):
    # Neither the base revision nor the working tree has a freeze manifest:
    # the gate reports not_run, which fails (freeze is never assumed).
    repo = FreezeRepo(tmp_path)
    git(repo.root, "rm", "-f", "package-freeze.toml")
    repo.commit_change("remove freeze manifest")
    report = repo.run(base=repo.extraction_revision)
    assert report.status == FAIL
    assert any(c.status == NOT_RUN for c in report.checks)
