"""contracts gate: supported-API comparison against a released baseline.

Pass criteria (docs/package-split/plan.md):

- a baseline artifact is supplied and readable (``--baseline <wheel>``) --
  missing baselines are never accepted automatically;
- every export declared in ``[contracts].supported_exports`` exists in both
  the baseline and the candidate, with the same kind, signature, defaults,
  annotations and (for exceptions) base classes;
- consumer-owned behavioral fixtures (``[contracts].behavioral_fixtures``)
  run against the candidate and pass.

The gate never writes or updates the baseline itself: it is purely
comparative, so there is no automatic baseline acceptance path.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess  # nosec B404
import sys
import tempfile
from typing import Any

from .. import wheelhouse
from ..evidence import FAIL
from ..evidence import NOT_RUN
from ..evidence import PASS
from ..evidence import SKIP
from ..manifest import Manifest


PROBE = Path(__file__).resolve().parent.parent / "probe.py"

# Attribute keys whose difference breaks compatibility.
_BREAKING_KEYS = ("kind", "signature", "defaults", "annotations", "bases", "metaclass")


def _snapshot(wheel: Path, workdir: Path, manifest: Manifest) -> dict[str, Any]:
    """Introspect a wheel's supported exports in a clean subprocess."""
    unpacked = wheelhouse.unpack_wheel(wheel, workdir / ("unpack-" + wheel.stem))
    spec = {
        "sys_path": [str(unpacked)],
        "supported_exports": manifest.supported_exports,
    }
    spec_file = workdir / ("spec-" + wheel.stem + ".json")
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    result = subprocess.run(  # nosec B603
        [sys.executable, "-I", str(PROBE), str(spec_file)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(workdir),
    )
    if result.returncode != 0:
        raise RuntimeError("snapshot probe failed: %s" % (result.stderr or result.stdout)[-2000:])
    snapshot: dict[str, Any] = json.loads(result.stdout)
    if not snapshot.get("contracts"):
        raise RuntimeError("snapshot probe produced no contracts section")
    contracts: dict[str, Any] = snapshot["contracts"]
    return contracts


def _diff(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for module, exports in baseline.items():
        if module not in candidate:
            differences.append({"module": module, "change": "module missing from candidate"})
            continue
        for export, description in exports.items():
            if export == "<module>":
                if "error" in description and "error" not in candidate[module].get(export, {}):
                    differences.append(
                        {"module": module, "export": export, "change": "baseline import error, candidate imports"}
                    )
                continue
            current = candidate[module].get(export, {})
            if current.get("missing"):
                differences.append({"module": module, "export": export, "change": "removed from candidate"})
                continue
            for key in _BREAKING_KEYS:
                if description.get(key) != current.get(key):
                    differences.append(
                        {
                            "module": module,
                            "export": export,
                            "change": "%s changed: %r -> %r" % (key, description.get(key), current.get(key)),
                        }
                    )
    return differences


def run_gate(manifest: Manifest, args: Any, report: Any) -> None:
    baseline_path = Path(args.baseline) if getattr(args, "baseline", None) else None
    workdir = Path(tempfile.mkdtemp(prefix="package-split-contracts-"))

    if baseline_path is None or not baseline_path.is_file():
        report.record(
            "baseline",
            NOT_RUN,
            "--baseline <released wheel> is required; a missing baseline never passes",
        )
        return

    candidate_path = Path(args.candidate) if getattr(args, "candidate", None) else None
    if candidate_path is None:
        # Build the candidate from the current checkout (same isolated build
        # as the artifacts gate) so the gate reflects the tree under test.
        try:
            artifacts = wheelhouse.build_distributions(
                manifest.root, workdir / "build", build_command=manifest.build_command
            )
            candidate_path = artifacts["wheel"]
        except wheelhouse.BuildError as exc:
            report.record("candidate-build", FAIL, str(exc)[:2000])
            return

    try:
        baseline = _snapshot(baseline_path, workdir, manifest)
        candidate = _snapshot(candidate_path, workdir, manifest)
    except Exception as exc:
        report.record("api-snapshot", FAIL, str(exc)[:2000])
        return

    differences = _diff(baseline, candidate)
    if differences:
        report.record(
            "api-compatibility",
            FAIL,
            "%d incompatible changes against the baseline" % len(differences),
            differences=differences[:200],
            baseline=str(baseline_path),
            baseline_sha256=wheelhouse.sha256_file(baseline_path),
            candidate=str(candidate_path),
            candidate_sha256=wheelhouse.sha256_file(candidate_path),
        )
    else:
        report.record(
            "api-compatibility",
            PASS,
            "supported exports match the baseline %s" % baseline_path.name,
            baseline=str(baseline_path),
            baseline_sha256=wheelhouse.sha256_file(baseline_path),
            candidate=str(candidate_path),
            candidate_sha256=wheelhouse.sha256_file(candidate_path),
        )

    # Behavioral fixtures run against the candidate (not the baseline): they
    # encode consumer-visible behavior signatures alone cannot capture.
    _behavioral_fixtures(manifest, report, candidate_path, workdir)


def _behavioral_fixtures(manifest: Manifest, report: Any, candidate: Path, workdir: Path) -> None:
    if not manifest.behavioral_fixtures:
        # No fixtures pinned yet: a visible skip rather than a failure, since
        # fixture definition is part of the still-being-pinned contract (the
        # plan requires fixtures before a contract is considered final).
        report.record(
            "behavioral-fixtures",
            SKIP,
            "no behavioral fixtures declared in [contracts].behavioral_fixtures "
            "(declare consumer-owned fixtures before pinning the contract)",
        )
        return
    failures: list[dict[str, Any]] = []
    for command in manifest.behavioral_fixtures:
        # Commands run in a clean subprocess with the candidate wheel's
        # unpacked tree prepended to the import path.
        unpacked = wheelhouse.unpack_wheel(candidate, workdir / "candidate-unpack")
        env_runner = _fixture_runner(command, unpacked)
        result = subprocess.run(  # nosec B603
            [sys.executable, "-I", str(env_runner)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(workdir),
        )
        entry = {
            "command": command,
            "returncode": result.returncode,
            "stderr": result.stderr[-1000:],
        }
        if result.returncode != 0:
            failures.append(entry)
    if failures:
        report.record("behavioral-fixtures", FAIL, "%d fixture(s) failed" % len(failures), failures=failures)
    else:
        report.record(
            "behavioral-fixtures",
            PASS,
            "%d behavioral fixture(s) passed against the candidate" % len(manifest.behavioral_fixtures),
        )


def _fixture_runner(command: list[str], unpacked: Path) -> Path:
    """Write a small runner that executes the fixture command with the
    candidate importable, and returns its path.
    """
    script = "\n".join(
        [
            "import os, subprocess, sys",
            "env = dict(os.environ)",
            "env['PYTHONPATH'] = %r + os.pathsep + env.get('PYTHONPATH', '')" % str(unpacked),
            "raise SystemExit(subprocess.call(%r, env=env))" % (command,),
        ]
    )
    runner = unpacked.parent / "fixture-runner.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(script, encoding="utf-8")
    return runner
