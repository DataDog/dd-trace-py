"""Compare extraction-to-head drift using only the protected base's freeze rules."""

from __future__ import annotations

import datetime
from pathlib import Path
import subprocess  # nosec B404
from typing import Any
from typing import Optional

from .._toml import tomllib
from ..evidence import FAIL
from ..evidence import NOT_RUN
from ..evidence import PASS
from ..evidence import SKIP
from ..freeze_manifest import FreezeManifestError
from ..freeze_manifest import validate_freeze
from ..manifest import Manifest


def run_gate(manifest: Manifest, args: Any, report: Any) -> None:
    base = getattr(args, "base", None)
    if not base:
        report.record("base-revision", NOT_RUN, "--base <target-revision> is required")
        return

    # The drift target is the head under test (a PR head in CI); record it
    # in the evidence so the result identifies the content it validated.
    head = getattr(args, "head", None) or "HEAD"
    report.inspected_head = _resolve_revision(manifest.root, head)

    freeze_path = Path(getattr(args, "freeze", None) or "package-freeze.toml")

    # Prefer the freeze manifest from the base revision: a PR must not be
    # able to weaken the gate by editing its own rules.
    content = _content_at_revision(manifest.root, base, freeze_path)
    source = "git revision %s" % base
    if content is None:
        report.record("freeze-manifest", NOT_RUN, "no readable protected freeze manifest at %r" % base)
        return

    # A PR cannot weaken the gate by editing its own rules: the enforced
    # rules always come from the base revision. But a PR that CHANGES the
    # rules (a candidate manifest) must be verified before it can become
    # the new base: its extraction revision must exist in this repository
    # and its recorded hashes must match the content at that revision.
    # Without this check, a syntactically valid manifest with a nonexistent
    # revision or wrong hashes would land and immediately fail the base
    # manifest integrity check for every subsequent PR.
    candidate_content = _content_at_revision(manifest.root, head, freeze_path)
    if candidate_content is not None and candidate_content != content:
        candidate_problems: list[str] = []
        try:
            candidate_data = tomllib.loads(candidate_content)
            candidate_issues = validate_freeze(candidate_data)
            if candidate_issues:
                candidate_problems.extend(candidate_issues)
            else:
                # Verify provenance: extraction revision and hashes must
                # exist and match in this repository.
                for entry in candidate_data.get("frozen", []):
                    rev = entry["extraction_revision"]
                    for path, recorded_hash in sorted(entry.get("hashes", {}).items()):
                        actual = _path_content(manifest.root, path, rev)
                        if actual is None:
                            candidate_problems.append(
                                "candidate frozen path %r is missing at extraction revision %r" % (path, rev)
                            )
                        else:
                            import hashlib as _hl

                            if _hl.sha256(actual).hexdigest() != recorded_hash:
                                candidate_problems.append(
                                    "candidate hash mismatch for %r at extraction revision %r" % (path, rev)
                                )
        except (FreezeManifestError, tomllib.TOMLDecodeError) as exc:
            candidate_problems.append(str(exc))
        if candidate_problems:
            report.record("candidate-rules", FAIL, "; ".join(candidate_problems)[:2000])
            return
        report.record(
            "candidate-rules",
            PASS,
            "PR modifies the freeze manifest; new rules verified (extraction revision and hashes match)",
        )

    # The package contract manifest is required for the gate to run: a PR
    # deleting it while the freeze stays active would leave every
    # subsequent PR unable to load its manifest, turning the required
    # freeze check into a repository-wide blocker.
    manifest_at_head = _content_at_revision(manifest.root, head, manifest.path.relative_to(manifest.root))
    if manifest_at_head is None:
        report.record(
            "candidate-rules",
            FAIL,
            "PR deletes package-contract.toml while a freeze is active: the freeze gate "
            "cannot run without its contract manifest",
        )
        return

    try:
        data = tomllib.loads(content)
        issues = validate_freeze(data)
        if issues:
            raise FreezeManifestError(freeze_path, issues)
    except (FreezeManifestError, tomllib.TOMLDecodeError) as exc:
        report.record("freeze-manifest", FAIL, str(exc)[:2000])
        return
    report.record("freeze-manifest", PASS, "validated (read from %s)" % source)

    try:
        problems, exempted = _verify_freeze(
            manifest.root, data, report, base=base, head=getattr(args, "head", None) or "HEAD"
        )
    except RuntimeError as exc:
        report.record("source-freeze", FAIL, str(exc))
        return
    if problems:
        report.record(
            "source-freeze",
            FAIL,
            "%d unauthorized change(s) to frozen paths" % len(problems),
            problems=problems,
            applied_exceptions=exempted,
        )
    else:
        report.record(
            "source-freeze",
            PASS,
            "all frozen paths match the extraction baseline",
            applied_exceptions=exempted,
        )
    if expired := data.get("freeze_exceptions", []):
        expired_paths = [e["path"] for e in expired if datetime.date.fromisoformat(e["expiry"]) < datetime.date.today()]
        if expired_paths:
            # Visible but not gate-failing: see the comment on exception
            # expiry handling in _verify_freeze.
            report.record(
                "freeze-exceptions",
                SKIP,
                "expired exception(s) no longer exempt drift: %s" % ", ".join(sorted(set(expired_paths))),
            )


def _resolve_revision(root: Path, revision: str) -> Optional[str]:
    """Resolve a revision argument (HEAD, a ref, a sha) to its full sha."""
    result = subprocess.run(  # nosec B603, B607 - fixed argv, PATH-resolved git
        ["git", "rev-parse", "--verify", "%s^{commit}" % revision],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip() if result.returncode == 0 else revision


def _content_at_revision(root: Path, revision: str, path: Path) -> Optional[str]:
    result = subprocess.run(  # nosec B603, B607 - fixed argv, PATH-resolved git
        ["git", "show", "%s:%s" % (revision, path.as_posix())],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        return result.stdout
    return None


def _git_lines(root: Path, args: list[str]) -> list[str]:
    result = subprocess.run(  # nosec B603, B607 - fixed argv, PATH-resolved git
        ["git"] + args,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError("git failed: %s" % result.stderr.strip())
    return result.stdout.rstrip("\0").split("\0") if result.stdout else []


def _path_content(root: Path, path: str, revision: Optional[str]) -> Optional[bytes]:
    if revision is not None:
        result = subprocess.run(  # nosec B603, B607 - fixed argv, PATH-resolved git
            ["git", "show", "%s:%s" % (revision, path)],
            cwd=str(root),
            capture_output=True,
            timeout=30,
        )
        return result.stdout if result.returncode == 0 else None
    file = root / path
    if file.is_file():
        return file.read_bytes()
    return None


def _is_regular_file(root: Path, path: str, revision: Optional[str]) -> bool:
    """Whether the path is a regular-file entry (not a symlink/special).

    A symlink to the approved content carries the same blob bytes and
    would otherwise pass a post_hash comparison while changing checkout
    and import behavior.
    """
    if revision is None:
        file = root / path
        return file.is_file() and not file.is_symlink()
    result = subprocess.run(  # nosec B603, B607 - fixed argv, PATH-resolved git
        ["git", "ls-tree", revision, "--", path],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return False
    return result.stdout.split()[0] in ("100644", "100755")


def _verify_freeze(
    root: Path, data: dict[str, Any], report: Any, base: str, head: str = "HEAD"
) -> tuple[list[str], list[str]]:
    import hashlib

    problems: list[str] = []
    exempted: list[str] = []
    today = datetime.date.today()

    # Exceptions must be current (never expired) with complete records;
    # active ones exempt their covered paths from the drift checks below
    # until they expire. An expired exception stops exempting -- drift
    # under it becomes unauthorized again -- but expiry alone is not a
    # gate failure: the rules are read from the PR base revision, so a
    # failing expiry would block the very PR that renews or removes the
    # entry (and every unrelated PR) with no bypass. Expired entries are
    # recorded in the evidence instead.
    active_exceptions: list[str] = []
    expired_exceptions: list[str] = []
    for exception in data.get("freeze_exceptions", []):
        expiry = datetime.date.fromisoformat(exception["expiry"])
        if expiry < today:
            expired_exceptions.append(exception["path"])
        else:
            active_exceptions.append(exception["path"].rstrip("/"))

    def _excepted(path: str) -> bool:
        return any(path == exc or path.startswith(exc + "/") for exc in active_exceptions)

    for entry in data.get("frozen", []):
        extraction_revision = entry["extraction_revision"]
        source_paths = entry["source_paths"]
        hashes = entry.get("hashes", {})
        rewrites = entry.get("approved_rewrites", [])
        approved = {rewrite["path"] for rewrite in rewrites}
        # A path may accumulate approved rewrites over time (e.g. an
        # upstream typing fix, then the extraction bridge); the head
        # content must match one of the pinned post-rewrite hashes.
        approved_hashes: dict[str, set[str]] = {}
        for rewrite in rewrites:
            approved_hashes.setdefault(rewrite["path"], set()).add(rewrite["post_hash"])

        # 1. Recorded hashes must match the content at the extraction
        # revision (freeze manifest integrity).
        for path, recorded_hash in sorted(hashes.items()):
            content = _path_content(root, path, extraction_revision)
            if content is None:
                problems.append("frozen path %r is missing at the extraction revision" % path)
            elif hashlib.sha256(content).hexdigest() != recorded_hash:
                problems.append("hash mismatch for %r at the extraction revision" % path)

        # 2. Drift between the extraction revision and the head under test
        # (a PR head in CI, or the current HEAD locally): unauthorized adds,
        # deletes, renames and edits to frozen paths. The base revision only
        # supplies the rules; measuring drift against the base would hide
        # exactly the changes a pull request introduces.
        name_status = _git_lines(
            root,
            [
                "diff",
                "--name-status",
                "--no-renames",
                "-z",
                extraction_revision,
                head,
                "--",
            ]
            + source_paths
            + entry.get("build_inputs", []),
        )

        def _check_rewritten_path(path: str, revision: str) -> None:
            """A rewritten path stays protected at the revision under test.

            The entry must be a regular file -- a symlink or special entry
            carrying the same blob would otherwise pass the post_hash check
            while changing checkout and import behavior -- and the content
            must match one of the approved post-rewrite contents (F19).
            """
            if not _is_regular_file(root, path, revision):
                problems.append(
                    "frozen path %r is not a regular file at the inspected revision: "
                    "symlinked or special entries are not accepted" % path
                )
                return
            content = _path_content(root, path, revision)
            content_hash = hashlib.sha256(content).hexdigest() if content is not None else None
            if content_hash is None or content_hash not in approved_hashes[path]:
                problems.append(
                    "frozen path %r has approved rewrites, but the content at the inspected "
                    "revision matches none of their post_hash values: further changes need a "
                    "new approved rewrite" % path
                )

        for status, path in zip(name_status[::2], name_status[1::2]):
            if _excepted(path):
                # An active, unexpired exception covers this path: the
                # drift is tolerated until the exception expires.
                exempted.append(path)
                continue
            if path in approved:
                # A rewritten path stays protected (F19): the drift is
                # tolerated only when the content is exactly one of the
                # approved post-rewrite contents, and the entry stays a
                # regular file.
                _check_rewritten_path(path, head)
                continue
            change = {"M": "edit", "A": "addition", "D": "deletion"}.get(status, "change")
            problems.append("unauthorized %s to frozen path %r" % (change, path))

        # 2b. Rewritten paths also keep their per-PR drift pinned. Once an
        # approved rewrite (e.g. the extraction bridge) lands on the base, a
        # PR reverting the file to its pre-extraction content shows no
        # cumulative drift (extraction_revision..head is empty) and would
        # silently remove the landed rewrite. The PR's own change must leave
        # rewritten paths at an approved content too. The comparison is
        # three-dot (merge-base..head): a PR that branched before a rewrite
        # landed on the base shows that base-only rewrite as its own change
        # in a two-dot diff, which would block unrelated older PRs.
        # Backward transitions are rejected: once a later rewrite has landed,
        # returning to an earlier approved content removes the newer rewrite
        # without a new approval, so the head must sit at the newest approved
        # content at or after the merge base's own rewrite index.
        pr_name_status = _git_lines(
            root,
            [
                "diff",
                "--name-status",
                "--no-renames",
                "-z",
                "%s...%s" % (base, head),
                "--",
            ]
            + source_paths
            + entry.get("build_inputs", []),
        )

        def _rewrite_index(path: str, revision: str) -> Optional[int]:
            """Index of the approved content present at a revision, or None."""
            content = _path_content(root, path, revision)
            content_hash = hashlib.sha256(content).hexdigest() if content is not None else None
            if content_hash is None:
                return None
            ordered_hashes = [rewrite["post_hash"] for rewrite in rewrites if rewrite["path"] == path]
            if content_hash in ordered_hashes:
                return ordered_hashes.index(content_hash)
            return None

        for status, path in zip(pr_name_status[::2], pr_name_status[1::2]):
            if path not in approved or _excepted(path):
                if path in approved and _excepted(path) and path not in exempted:
                    exempted.append(path)
                continue
            base_index = _rewrite_index(path, base)
            head_index = _rewrite_index(path, head)
            if base_index is not None and head_index is not None and head_index < base_index:
                problems.append(
                    "frozen path %r reverts to an older approved rewrite: returning to a "
                    "superseded post_hash needs a new approved rewrite" % path
                )
                continue
            _check_rewritten_path(path, head)

        # 3. Approved rewrites are recorded as evidence; the drift check
        # above pins each one to its exact approved content, and active
        # exceptions exempted paths are visible in the report data.
        rewrite_evidence = [{"path": r["path"], "reason": r["reason"], "approver": r["approver"]} for r in rewrites]
        if rewrite_evidence:
            report.record(
                "approved-rewrites",
                PASS,
                "%d approved rewrite(s) tolerated" % len(rewrites),
                rewrites=rewrites,
            )

    return problems, exempted
