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
from package_split.evidence import SKIP
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

REWRITE_TEMPLATE = """
[[frozen.approved_rewrites]]
path = "sample/mod.py"
reason = "compatibility shim"
approver = "test-owner"
sync_record = "recorded"
post_hash = "{post_hash}"
"""

# Legacy-style rewrite (path-scoped, no pinned content) -- rejected since F19.
REWRITE_WITHOUT_HASH = """
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
    # The approved rewrite pins the exact post-rewrite content; the head
    # matches it.
    rewrites = REWRITE_TEMPLATE.format(post_hash=hashlib.sha256(b"value = 2\n").hexdigest())
    repo = FreezeRepo(tmp_path, rewrites=rewrites)
    (repo.root / "sample" / "mod.py").write_text("value = 2\n", encoding="utf-8")
    repo.commit_change("approved rewrite")
    report = repo.run()
    assert report.status == PASS, report.summary()
    assert any(c.name == "approved-rewrites" for c in report.checks)


def test_second_edit_after_approved_rewrite_fails(tmp_path):
    # F19: an approved rewrite authorizes exactly its pinned content, not
    # the path -- a second, different edit to the rewritten path is
    # unauthorized drift.
    rewrites = REWRITE_TEMPLATE.format(post_hash=hashlib.sha256(b"value = 2\n").hexdigest())
    repo = FreezeRepo(tmp_path, rewrites=rewrites)
    (repo.root / "sample" / "mod.py").write_text("value = 3\n", encoding="utf-8")
    repo.commit_change("second, unapproved edit")
    report = repo.run()
    assert report.status == FAIL
    source_freeze = [c for c in report.checks if c.name == "source-freeze"]
    assert source_freeze and source_freeze[0].status == FAIL
    assert any("post_hash" in p for p in source_freeze[0].data["problems"])


def test_rewrite_without_post_hash_is_rejected(tmp_path):
    # Path-scoped rewrites pre-authorize every future edit to the path
    # (F19); manifests without pinned post-rewrite content are invalid.
    repo = FreezeRepo(tmp_path, rewrites=REWRITE_WITHOUT_HASH)
    (repo.root / "sample" / "mod.py").write_text("value = 2\n", encoding="utf-8")
    repo.commit_change("rewrite with a legacy path-scoped approval")
    report = repo.run()
    assert report.status == FAIL
    freeze_manifest = [c for c in report.checks if c.name == "freeze-manifest"]
    assert freeze_manifest and freeze_manifest[0].status == FAIL
    assert "post_hash" in str(freeze_manifest[0].detail)


def test_multiple_approved_rewrites_for_a_path(tmp_path):
    # Sequential rewrites of the same path (an upstream fix, then the
    # extraction bridge): the head must match one of the pinned contents.
    approved_content = "value = 2\n"
    bridged_content = "value = 2  # bridged\n"
    rewrites = REWRITE_TEMPLATE.format(
        post_hash=hashlib.sha256(approved_content.encode()).hexdigest()
    ) + REWRITE_TEMPLATE.format(post_hash=hashlib.sha256(bridged_content.encode()).hexdigest())
    repo = FreezeRepo(tmp_path, rewrites=rewrites)
    (repo.root / "sample" / "mod.py").write_text(bridged_content, encoding="utf-8")
    repo.commit_change("extraction bridge")
    report = repo.run()
    assert report.status == PASS, report.summary()


def test_active_exception_exempts_drift(tmp_path):
    # Active (unexpired) exceptions exempt their covered paths from the
    # drift checks; the exemption is visible in the evidence.
    exceptions = EXCEPTION.format(expiry="2999-01-01")
    repo = FreezeRepo(tmp_path, exceptions=exceptions)
    (repo.root / "sample" / "mod.py").write_text("value = 2\n", encoding="utf-8")
    repo.commit_change("excepted edit")
    report = repo.run()
    assert report.status == PASS, report.summary()
    source_freeze = [c for c in report.checks if c.name == "source-freeze"]
    assert source_freeze and source_freeze[0].data.get("applied_exceptions") == ["sample/mod.py"]


def test_inspected_head_recorded_in_evidence(tmp_path):
    # The report records the head actually inspected (resolved to a sha),
    # not just the checkout HEAD: a PR-head run must identify the content
    # it validated.
    repo = FreezeRepo(tmp_path)
    git(repo.root, "checkout", "-b", "pr-head")
    pr_head_sha = git(repo.root, "rev-parse", "HEAD")
    git(repo.root, "checkout", "main")

    report = repo.run(base="main", head="pr-head")
    assert report.inspected_head == pr_head_sha
    evidence = report.to_dict()
    assert evidence["inspected_head"] == pr_head_sha


def test_expired_exception_without_drift_is_visible_not_blocking(tmp_path):
    # Expired exceptions stop exempting but do not fail the gate on their
    # own: rules are read from the PR base, so a failing expiry would block
    # the very PR that renews or removes the entry (and every unrelated
    # PR). The expiry is visible as a freeze-exceptions skip instead.
    exceptions = EXCEPTION.format(expiry="2001-01-01")
    repo = FreezeRepo(tmp_path, exceptions=exceptions)
    report = repo.run()
    assert report.status == PASS, report.summary()
    freeze_exceptions = [c for c in report.checks if c.name == "freeze-exceptions"]
    assert freeze_exceptions and freeze_exceptions[0].status == SKIP
    assert "sample/" in freeze_exceptions[0].detail


def test_expired_exception_drift_becomes_unauthorized(tmp_path):
    # Once the exception expires, drift under its path is unauthorized
    # again -- the time box is enforced by the drift check, not by
    # failing every unrelated PR.
    exceptions = EXCEPTION.format(expiry="2001-01-01")
    repo = FreezeRepo(tmp_path, exceptions=exceptions)
    (repo.root / "sample" / "mod.py").write_text("value = 2\n", encoding="utf-8")
    repo.commit_change("edit under an expired exception")
    report = repo.run()
    assert report.status == FAIL
    assert any(
        "unauthorized edit" in p for c in report.checks if c.name == "source-freeze" for p in c.data.get("problems", [])
    )


def test_revert_of_landed_rewrite_fails(tmp_path):
    # Once an approved rewrite (the bridge) lands on the base, a PR
    # reverting the file to its pre-extraction content shows no cumulative
    # drift (extraction..head is empty); the PR's own change (base..head)
    # must still leave the rewritten path at an approved content.
    rewrites = REWRITE_TEMPLATE.format(post_hash=hashlib.sha256(b"value = 2\n").hexdigest())
    repo = FreezeRepo(tmp_path, rewrites=rewrites)
    original = (repo.root / "sample" / "mod.py").read_text()
    bridged = original + "# bridge\n"
    # Land the bridge on the default branch (the base).
    (repo.root / "sample" / "mod.py").write_text(bridged, encoding="utf-8")
    repo.commit_change("land the bridge")
    # PR: revert to the pre-extraction content.
    git(repo.root, "checkout", "-b", "pr-head")
    (repo.root / "sample" / "mod.py").write_text(original, encoding="utf-8")
    repo.commit_change("revert the bridge")
    git(repo.root, "checkout", "main")

    report = repo.run(base="main", head="pr-head")
    assert report.status == FAIL
    assert any("post_hash" in p for c in report.checks if c.name == "source-freeze" for p in c.data.get("problems", []))


def test_symlink_with_matching_content_fails(tmp_path):
    # A symlink entry whose blob bytes equal the approved content passes a
    # naive post_hash comparison while changing checkout/import behavior;
    # the entry type is part of the approval.
    rewrites = REWRITE_TEMPLATE.format(post_hash=hashlib.sha256(b"value = 2\n").hexdigest())
    repo = FreezeRepo(tmp_path, rewrites=rewrites)
    # Replace the regular file with a symlink whose target string has the
    # exact approved content bytes.
    target = repo.root / "sample" / "value = 2\n"
    target.write_bytes(b"")
    mod = repo.root / "sample" / "mod.py"
    mod.unlink()
    mod.symlink_to(target)
    repo.commit_change("rewrite as a symlink with identical content")
    report = repo.run()
    assert report.status == FAIL
    assert any(
        "not a regular file" in p
        for c in report.checks
        if c.name == "source-freeze"
        for p in c.data.get("problems", [])
    )


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


def test_merge_base_comparison_not_base_only_changes(tmp_path):
    # A PR branched before the rewrite landed on the base: its unchanged
    # frozen file carries the extraction baseline, and a two-dot base..head
    # diff would show the base-only rewrite as the PR's own change. The
    # three-dot (merge-base..head) comparison must not block it.
    rewrites = REWRITE_TEMPLATE.format(post_hash=hashlib.sha256(b"value = 2\n").hexdigest())
    repo = FreezeRepo(tmp_path, rewrites=rewrites)
    # Land the rewrite on the default branch (the base).
    (repo.root / "sample" / "mod.py").write_text("value = 2\n", encoding="utf-8")
    repo.commit_change("land the rewrite")
    # An older PR whose branch still carries the pre-extraction baseline.
    git(repo.root, "checkout", "-b", "older-pr", "HEAD~1")
    git(repo.root, "checkout", "main")

    report = repo.run(base="main", head="older-pr")
    assert report.status == PASS, report.summary()


def test_revert_to_older_approved_rewrite_fails(tmp_path):
    # After the second approved rewrite lands on the base, a PR replacing
    # the current content with the FIRST (older) approved content passes a
    # membership test over all historical post_hash values. Backward
    # transitions must be rejected.
    approved_content = "value = 2\n"
    bridged_content = "value = 3\n"
    rewrites = REWRITE_TEMPLATE.format(
        post_hash=hashlib.sha256(approved_content.encode()).hexdigest()
    ) + REWRITE_TEMPLATE.format(post_hash=hashlib.sha256(bridged_content.encode()).hexdigest())
    repo = FreezeRepo(tmp_path, rewrites=rewrites)
    # The bridge (second rewrite) lands on the base.
    (repo.root / "sample" / "mod.py").write_text(bridged_content, encoding="utf-8")
    repo.commit_change("land the bridge")
    # PR: revert to the older approved content.
    git(repo.root, "checkout", "-b", "pr-head")
    (repo.root / "sample" / "mod.py").write_text(approved_content, encoding="utf-8")
    repo.commit_change("revert to the older approved rewrite")
    git(repo.root, "checkout", "main")

    report = repo.run(base="main", head="pr-head")
    assert report.status == FAIL
    assert any(
        "older approved rewrite" in p
        for c in report.checks
        if c.name == "source-freeze"
        for p in c.data.get("problems", [])
    )


def test_unknown_frozen_entry_key_rejected(tmp_path):
    # A typo in an optional field (build_inputs) would validate cleanly
    # and then be silently ignored, leaving the intended protection unset.
    repo = FreezeRepo(tmp_path)
    freeze_text = (tmp_path / "package-freeze.toml").read_text(encoding="utf-8")
    freeze_text = freeze_text.replace('build_inputs = ["pyproject.toml"]', 'build_input = ["pyproject.toml"]')
    (tmp_path / "package-freeze.toml").write_text(freeze_text, encoding="utf-8")
    repo.commit_change("typo in build_inputs")
    report = repo.run(base="HEAD")
    assert report.status == FAIL
    freeze_manifest = [c for c in report.checks if c.name == "freeze-manifest"]
    assert freeze_manifest and freeze_manifest[0].status == FAIL
    assert "build_input" in str(freeze_manifest[0].detail)
