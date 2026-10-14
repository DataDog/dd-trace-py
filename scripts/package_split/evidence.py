"""Gate evidence: structured check records and JSON reports.

Every gate records individual pass/fail/skip/not_run checks plus environment
metadata (source revision, manifest hash, harness version) into a JSON
evidence file. Rules enforced here, per docs/package-split/plan.md:

- A gate passes only when no check failed.
- A ``not_run`` check (missing prerequisite or unexecuted required scenario)
  never counts as passing; it fails the gate.
- A ``skip`` is allowed only with an explicit reason (optional scenarios,
  environment-dependent tools) and is always visible in the summary.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
import datetime
import hashlib
import json
from pathlib import Path
import platform
import subprocess  # nosec B404
import sys
from typing import Any
from typing import Optional


PASS = "pass"  # nosec B105 - gate status label, not a secret
FAIL = "fail"
SKIP = "skip"
NOT_RUN = "not_run"

GATE_STATUSES = (PASS, FAIL)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _git_revision(path: Path) -> str:
    try:
        return subprocess.run(  # nosec B603, B607 - fixed argv, PATH-resolved git
            ["git", "rev-parse", "HEAD"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _file_sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass
class Check:
    """One named, individually reported gate check."""

    name: str
    status: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class GateReport:
    """Accumulates checks for one gate run and renders evidence + summary."""

    def __init__(self, gate: str, manifest_path: Path, harness_version: str) -> None:
        self.gate = gate
        self.manifest_path = manifest_path
        self.manifest_sha256 = _file_sha256(manifest_path)
        self.harness_version = harness_version
        self.started = _utc_now()
        self.finished: Optional[str] = None
        self.error: Optional[str] = None
        self.source_revision = _git_revision(manifest_path.parent or Path("."))
        self.environment: dict[str, Any] = {
            "python": sys.version,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "executable": sys.executable,
        }
        self.checks: list[Check] = []

    def record(self, name: str, status: str, detail: str = "", **data: Any) -> Check:
        if status not in (PASS, FAIL, SKIP, NOT_RUN):
            raise ValueError("invalid check status: %r" % (status,))
        check = Check(name=name, status=status, detail=detail, data=data)
        self.checks.append(check)
        return check

    def record_subchecks(self, prefix: str, checks: Iterable[Check]) -> None:
        for check in checks:
            self.record(
                "%s.%s" % (prefix, check.name),
                check.status,
                check.detail,
                **check.data,
            )

    @property
    def status(self) -> str:
        # The gate itself only has pass/fail: skips are visible but neutral,
        # while not_run (unexecuted required work) and any failure both fail.
        if self.error is not None:
            return FAIL
        for check in self.checks:
            if check.status in (FAIL, NOT_RUN):
                return FAIL
        return PASS

    @property
    def exit_code(self) -> int:
        return 0 if self.status == PASS else 1

    def summary(self) -> str:
        lines = [
            "package-split gate '%s': %s" % (self.gate, self.status.upper()),
            "  harness: %s  manifest: %s (sha256 %s)"
            % (self.harness_version, self.manifest_path, self.manifest_sha256),
            "  source revision: %s" % self.source_revision,
        ]
        if self.error is not None:
            lines.append("  gate error: %s" % self.error)
        for check in self.checks:
            detail = (" -- " + check.detail) if check.detail else ""
            lines.append("  [%s] %-40s%s" % (check.status, check.name, detail))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        self.finished = self.finished or _utc_now()
        return {
            "gate": self.gate,
            "status": self.status,
            "harness_version": self.harness_version,
            "manifest": {"path": str(self.manifest_path), "sha256": self.manifest_sha256},
            "source_revision": self.source_revision,
            "started": self.started,
            "finished": self.finished,
            "environment": self.environment,
            "error": self.error,
            "summary": self.summary(),
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail, "data": c.data} for c in self.checks],
        }

    def write(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        evidence = output_dir / ("%s.json" % self.gate)
        evidence.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n")
        return evidence

    def finish(self, output_dir: Path) -> Path:
        """Freeze timestamps, write the evidence file, return its path."""
        self.finished = _utc_now()
        return self.write(output_dir)
