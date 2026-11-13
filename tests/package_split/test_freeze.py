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
import pytest

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
schema_version = 2

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

APPROVED_SOURCE = "value = 2\n"

REWRITE = f"""
[[frozen.approved_rewrites]]
path = "sample/mod.py"
reason = "compatibility shim"
approver = "test-owner"
sync_record = "recorded"
post_hash = "{hashlib.sha256(APPROVED_SOURCE.encode()).hexdigest()}"
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
        report = make_report("freeze", self.root / "package-freeze.toml")
        freeze.run_gate(self.root, Args(base=base, head=head), report)
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
    (repo.root / "sample" / "mod.py").write_text(APPROVED_SOURCE, encoding="utf-8")
    repo.commit_change("approved rewrite")
    report = repo.run()
    assert report.status == PASS, report.summary()
    assert any(c.name == "approved-rewrites" for c in report.checks)


@pytest.mark.parametrize("source", ["value = 3\n", "value = 1\n", None])
def test_approved_rewrite_rejects_other_content(tmp_path, source):
    repo = FreezeRepo(tmp_path, rewrites=REWRITE)
    path = repo.root / "sample" / "mod.py"
    path.write_text(APPROVED_SOURCE, encoding="utf-8")
    repo.commit_change("approved rewrite")
    if source is None:
        path.unlink()
    else:
        path.write_text(source, encoding="utf-8")
    repo.commit_change("unapproved content")
    report = repo.run()
    assert report.status == FAIL, report.summary()
    assert any("approved rewrite" in str(check.data.get("problems", [])) for check in report.checks)
    assert not any(check.name == "approved-rewrites" and check.status == PASS for check in report.checks)


@pytest.mark.parametrize("post_hash", [None, "", "x" * 64, "A" * 64])
def test_approved_rewrite_requires_sha256(tmp_path, post_hash):
    rewrite = "\n".join(line for line in REWRITE.splitlines() if not line.startswith("post_hash"))
    if post_hash is not None:
        rewrite += '\npost_hash = "%s"\n' % post_hash
    repo = FreezeRepo(tmp_path, rewrites=rewrite)
    report = repo.run()
    assert report.status == FAIL, report.summary()
    assert any(check.name == "freeze-manifest" and "post_hash" in check.detail for check in report.checks)


def test_pr_cannot_reapprove_its_own_rewrite(tmp_path):
    repo = FreezeRepo(tmp_path, rewrites=REWRITE)
    base = git(repo.root, "rev-parse", "HEAD")
    (repo.root / "sample" / "mod.py").write_text("value = 3\n", encoding="utf-8")
    manifest = repo.root / "package-freeze.toml"
    manifest.write_text(
        manifest.read_text().replace(
            hashlib.sha256(APPROVED_SOURCE.encode()).hexdigest(), sha256(repo.root / "sample/mod.py")
        ),
        encoding="utf-8",
    )
    repo.commit_change("change source and approve it on the PR head")
    report = repo.run(base=base)
    assert report.status == FAIL, report.summary()
    assert any("approved rewrite" in str(check.data.get("problems", [])) for check in report.checks)
    protected = subprocess.check_output(["git", "show", "%s:package-freeze.toml" % base], cwd=repo.root)
    assert report.manifest_sha256 == hashlib.sha256(protected).hexdigest()


def test_git_failure_never_passes(tmp_path):
    repo = FreezeRepo(tmp_path)
    report = repo.run(head="nonexistent-revision")
    assert report.status == FAIL
    assert any("git failed" in check.detail for check in report.checks)


def test_freeze_does_not_trust_working_tree_fallback(tmp_path):
    repo = FreezeRepo(tmp_path)
    assert repo.run(base=repo.extraction_revision).status == FAIL


def test_freeze_covers_build_inputs(tmp_path):
    repo = FreezeRepo(tmp_path)
    (tmp_path / "pyproject.toml").write_text("# changed build input\n", encoding="utf-8")
    repo.commit_change("build drift")
    assert repo.run().status == FAIL


def test_old_freeze_schema_is_rejected(tmp_path):
    repo = FreezeRepo(tmp_path)
    manifest = repo.root / "package-freeze.toml"
    manifest.write_text(manifest.read_text().replace("schema_version = 2", "schema_version = 1"), encoding="utf-8")
    repo.commit_change("old freeze schema")
    report = repo.run()
    assert report.status == FAIL, report.summary()
    assert any(check.name == "freeze-manifest" and "schema_version" in check.detail for check in report.checks)


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
