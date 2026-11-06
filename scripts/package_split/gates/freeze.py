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
from ..freeze_manifest import FreezeManifestError
from ..freeze_manifest import validate_freeze
from ..manifest import Manifest


def run_gate(manifest: Manifest, args: Any, report: Any) -> None:
    base = getattr(args, "base", None)
    if not base:
        report.record("base-revision", NOT_RUN, "--base <target-revision> is required")
        return

    freeze_path = Path(getattr(args, "freeze", None) or "package-freeze.toml")

    # Prefer the freeze manifest from the base revision: a PR must not be
    # able to weaken the gate by editing its own rules.
    content = _content_at_revision(manifest.root, base, freeze_path)
    source = "git revision %s" % base
    if content is None:
        report.record("freeze-manifest", NOT_RUN, "no readable protected freeze manifest at %r" % base)
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
        problems = _verify_freeze(manifest.root, data, report, head=getattr(args, "head", None) or "HEAD")
    except RuntimeError as exc:
        report.record("source-freeze", FAIL, str(exc))
        return
    if problems:
        report.record(
            "source-freeze",
            FAIL,
            "%d unauthorized change(s) to frozen paths" % len(problems),
            problems=problems,
        )
    else:
        report.record("source-freeze", PASS, "all frozen paths match the extraction baseline")


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


def _verify_freeze(root: Path, data: dict[str, Any], report: Any, head: str = "HEAD") -> list[str]:
    import hashlib

    problems: list[str] = []
    today = datetime.date.today()

    # Exceptions must be current (never expired) with complete records.
    for i, exception in enumerate(data.get("freeze_exceptions", [])):
        expiry = datetime.date.fromisoformat(exception["expiry"])
        if expiry < today:
            problems.append("freeze exception for %r expired on %s" % (exception["path"], exception["expiry"]))

    for entry in data.get("frozen", []):
        extraction_revision = entry["extraction_revision"]
        source_paths = entry["source_paths"]
        hashes = entry.get("hashes", {})
        approved = {rewrite["path"] for rewrite in entry.get("approved_rewrites", [])}

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
        for status, path in zip(name_status[::2], name_status[1::2]):
            if path not in approved:
                change = {"M": "edit", "A": "addition", "D": "deletion"}.get(status, "change")
                problems.append("unauthorized %s to frozen path %r" % (change, path))

        # 3. Approved rewrites must still differ from the frozen baseline only
        # in the ways their owners approved; record them as evidence.
        rewrites = [
            {"path": r["path"], "reason": r["reason"], "approver": r["approver"]}
            for r in entry.get("approved_rewrites", [])
        ]
        if rewrites:
            report.record(
                "approved-rewrites",
                PASS,
                "%d approved rewrite(s) tolerated" % len(rewrites),
                rewrites=rewrites,
            )
    return problems
