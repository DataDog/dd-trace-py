"""Freeze gate: block any change to a frozen file's content hash.

A frozen file is a file being extracted to the destination repository
(datadog-apm-python). Once its content hash is pinned in
package-freeze.toml, any pull request that changes that file's content
fails this gate. There are no exemptions -- the freeze is lifted only by
removing the path from package-freeze.toml in a change that itself does
not touch any other frozen path.

Both the frozen hashes and the PR-head content are read via ``git show``
(not the working tree), so the check is against the exact revisions the
CI supplies. The freeze manifest is loaded from the base revision so a
PR cannot weaken its own rules by editing the manifest.
"""

from __future__ import annotations

import subprocess  # nosec B404 - fixed argv, trusted git binary
from typing import Any

from ..evidence import FAIL
from ..evidence import PASS
from ..freeze_manifest import load_freeze
from ..manifest import Manifest


def _git_show(revision: str, path: str) -> str | None:
    """Return the content of a path at a revision, or None if it does not exist.

    Raises RuntimeError for any failure other than the path being absent,
    so an invalid revision or a git error fails the gate instead of
    silently reporting PASS.
    """
    result = subprocess.run(  # nosec B603, B607 - fixed argv
        ["git", "show", f"{revision}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout
    stderr = result.stderr.strip()
    # git reports a missing path differently from a missing revision.
    if "does not exist" in stderr or "exists on disk, but not in" in stderr:
        return None
    raise RuntimeError(f"git show {revision}:{path} failed: {stderr}")


def _git_blob_hash(revision: str, path: str) -> str | None:
    """Return the SHA-256 of a file's content at a revision, or None if absent."""
    content = _git_show(revision, path)
    if content is None:
        return None
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def run_gate(manifest: Manifest, args: Any, report: Any) -> None:
    """Check that no frozen file's content hash changed between base and head."""
    base_ref = getattr(args, "base", None) or "origin/main"
    head_ref = getattr(args, "head", None) or "HEAD"
    freeze_path = getattr(args, "freeze", None) or "package-freeze.toml"

    # Load the freeze manifest from the base revision (not the PR head),
    # so a PR cannot weaken its own rules by editing the manifest.
    manifest_content = _git_show(base_ref, freeze_path)
    if manifest_content is None:
        report.record("freeze", PASS, f"no {freeze_path} on the base; nothing is frozen")
        return

    from pathlib import Path
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(manifest_content)
        manifest_file = Path(f.name)

    try:
        frozen = load_freeze(manifest_file)
    except ValueError as exc:
        report.record("freeze", FAIL, f"invalid freeze manifest on {base_ref}: {exc}")
        return
    finally:
        manifest_file.unlink(missing_ok=True)

    if not frozen:
        report.record("freeze", PASS, "freeze manifest is empty; nothing is frozen")
        return

    violations = []
    for file_path, expected_hash in sorted(frozen.items()):
        # Read the content from the head revision, not the working tree,
        # so the check is against the exact commit CI is validating.
        actual_hash = _git_blob_hash(head_ref, file_path)
        if actual_hash is None:
            violations.append(f"{file_path}: frozen file deleted at {head_ref}")
        elif actual_hash != expected_hash:
            violations.append(
                f"{file_path}: content hash changed (expected {expected_hash[:12]}, got {actual_hash[:12]})"
            )

    if violations:
        report.record(
            "freeze",
            FAIL,
            "%d frozen file(s) changed; remove the path from %s to lift the freeze" % (len(violations), freeze_path),
            violations=violations,
        )
    else:
        report.record("freeze", PASS, "all %d frozen file(s) unchanged" % len(frozen))
