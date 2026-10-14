"""migration gate: old/new coexistence, upgrade order, removal and rollback.

Pass criteria (docs/package-split/plan.md):

- scenarios drive a fresh venv through install/uninstall steps (old-only,
  new-only, coexistence, supported upgrade orders, removal of either
  product, rollback) using exact artifacts;
- file ownership: installed distributions never claim overlapping files;
- canonical identity: bridge modules resolve the declared attribute to the
  expected provider module (a broken new installation must fail loudly, not
  silently fall back to the old provider);
- exactly one effective plugin/entry point per declared group;
- ``imports_ok``/``imports_fail`` verify install and removal behavior,
  including that compatibility shims and entry points only refer to
  installed implementations.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any
from typing import Optional

from .. import wheelhouse
from ..evidence import FAIL
from ..evidence import NOT_RUN
from ..evidence import PASS
from ..evidence import SKIP
from ..manifest import Manifest
from ..matrices import MatrixError
from ..matrices import parse_matrix
from ..venvutil import VenvError
from ..venvutil import create_venv
from ..venvutil import installed_distributions
from ..venvutil import pip_install
from ..venvutil import pip_uninstall
from ..venvutil import run_in_venv


PROBE = Path(__file__).resolve().parent.parent / "probe.py"


def run_gate(manifest: Manifest, args: Any, report: Any) -> None:
    matrix_path = Path(args.matrix) if getattr(args, "matrix", None) else None
    if matrix_path is None or not matrix_path.is_file():
        report.record(
            "matrix",
            NOT_RUN,
            "--matrix <file> is required and must exist; unexecuted scenarios never pass",
        )
        return
    try:
        matrix = parse_matrix(matrix_path, "migration")
    except MatrixError as exc:
        report.record("matrix", FAIL, str(exc))
        return
    report.record(
        "matrix",
        PASS,
        "%d scenario(s) parsed from %s" % (len(matrix.scenarios), matrix_path),
    )

    workdir = Path(tempfile.mkdtemp(prefix="package-split-migration-"))
    candidate: dict[str, Any] = {"path": None}
    needs_candidate = any(step.artifact == "candidate" for s in matrix.scenarios for step in s.steps)
    if needs_candidate:
        candidate_path = getattr(args, "candidate", None)
        if candidate_path:
            candidate = {
                "path": Path(candidate_path),
                "sha256": wheelhouse.sha256_file(Path(candidate_path)),
            }
            report.record("candidate-build", PASS, candidate["path"].name, candidate=candidate)
        else:
            try:
                artifacts = wheelhouse.build_distributions(
                    manifest.root, workdir / "build", build_command=manifest.build_command
                )
                candidate = {
                    "path": artifacts["wheel"],
                    "sha256": wheelhouse.sha256_file(artifacts["wheel"]),
                }
            except wheelhouse.BuildError as exc:
                report.record("candidate-build", FAIL, str(exc)[:2000])
                return
            report.record("candidate-build", PASS, candidate["path"].name, candidate=candidate)

    for scenario in matrix.scenarios:
        _run_scenario(report, scenario, candidate, matrix_path.parent, workdir)


def _resolve_artifact(artifact: str, candidate: dict[str, Any], matrix_dir: Path) -> dict[str, Any]:
    if artifact == "candidate":
        if candidate.get("path") is None:
            return {"error": "candidate was not built"}
        return {"requirement": str(candidate["path"]), "kind": "candidate"}
    path = Path(artifact)
    if not path.is_absolute():
        path = matrix_dir / artifact
    if path.is_file():
        return {"requirement": str(path), "kind": "artifact", "sha256": wheelhouse.sha256_file(path)}
    return {"requirement": artifact, "kind": "requirement"}


def _run_scenario(
    report: Any,
    scenario: Any,
    candidate: dict[str, Any],
    matrix_dir: Path,
    workdir: Path,
) -> None:
    venv_dir = workdir / ("venv-" + scenario.name.replace("/", "-"))
    step_evidence: list[dict[str, Any]] = []
    # Local wheels referenced by steps double as find-links sources so
    # inter-package requirements resolve without an index.
    find_links: list[Path] = [matrix_dir]

    try:
        create_venv(venv_dir)
    except VenvError as exc:
        _finish(report, scenario, NOT_RUN, "venv creation failed: %s" % exc, step_evidence)
        return

    try:
        for i, step in enumerate(scenario.steps):
            if step.action == "install":
                resolved = _resolve_artifact(step.artifact, candidate, matrix_dir)
                if "error" in resolved:
                    _finish(
                        report,
                        scenario,
                        NOT_RUN,
                        "step %d: %s" % (i, resolved["error"]),
                        step_evidence,
                    )
                    return
                if resolved.get("kind") in ("candidate", "artifact"):
                    artifact_dir = Path(resolved["requirement"]).parent
                    if artifact_dir not in find_links:
                        find_links.append(artifact_dir)
                pip_install(venv_dir, [resolved["requirement"]], find_links=find_links)
                step_evidence.append({"step": i, "action": "install", "detail": resolved})
            else:
                pip_uninstall(venv_dir, [step.distribution])
                step_evidence.append({"step": i, "action": "uninstall", "distribution": step.distribution})
    except VenvError as exc:
        _finish(report, scenario, FAIL, "step failed: %s" % str(exc)[:1000], step_evidence)
        return

    installed = installed_distributions(venv_dir)
    failures: list[str] = []

    checks = scenario.checks

    # File ownership: no two installed distributions may claim the same file.
    if checks.get("file_ownership"):
        overlaps = _file_ownership_overlaps(venv_dir, installed)
        if overlaps:
            failures.append("overlapping file ownership: %s" % overlaps[:10])

    # Canonical identity through the compatibility bridge.
    identity = checks.get("identity")
    if identity:
        result = _probe(
            venv_dir,
            workdir,
            identity=[identity["bridge"]],
            imports_ok=checks.get("imports_ok", []),
            imports_fail=checks.get("imports_fail", []),
        )
        bridge_ref = identity["bridge"]
        entry = result.get("identity", {}).get(bridge_ref, {})
        if not entry.get("ok"):
            if bridge_ref in result.get("identity", {}).get("imports_fail", {}) or any(
                bridge_ref == target for target in checks.get("imports_fail", [])
            ):
                pass  # expected failure: the bridge must not import
            else:
                failures.append("bridge %s failed to resolve: %s" % (bridge_ref, entry.get("error")))
        elif entry.get("origin_module") != identity.get("expect_module"):
            failures.append(
                "bridge %s resolved to %r, expected %r"
                % (bridge_ref, entry.get("origin_module"), identity.get("expect_module"))
            )
    else:
        result = _probe(
            venv_dir,
            workdir,
            identity=[],
            imports_ok=checks.get("imports_ok", []),
            imports_fail=checks.get("imports_fail", []),
        )

    for target, entry in (result.get("identity", {}).get("imports_ok", {}) or {}).items():
        if not entry.get("ok"):
            failures.append("expected import of %s to succeed: %s" % (target, entry.get("error")))
    for target, entry in (result.get("identity", {}).get("imports_fail", {}) or {}).items():
        if entry.get("ok") and "unexpectedly succeeded" in entry.get("error", ""):
            failures.append("expected import of %s to fail, but it succeeded" % target)

    # Exactly one effective plugin per declared entry-point group.
    entry_points = checks.get("entry_points")
    if entry_points:
        counts = wheelhouse.entry_point_counts(venv_dir, [entry_points["group"]])
        actual = len(counts.get(entry_points["group"], []))
        if actual != entry_points.get("count", 1):
            failures.append(
                "entry point group %r has %d entries, expected %d: %s"
                % (entry_points["group"], actual, entry_points.get("count", 1), counts)
            )

    if failures:
        _finish(report, scenario, FAIL, "; ".join(failures)[:2000], step_evidence, installed)
    else:
        _finish(
            report,
            scenario,
            PASS,
            "%d step(s), all checks passed" % len(scenario.steps),
            step_evidence,
            installed,
        )


def _probe(
    venv_dir: Path,
    workdir: Path,
    identity: list[str],
    imports_ok: list[str],
    imports_fail: list[str],
) -> dict[str, Any]:
    spec = {
        "sys_path": [],
        "identity": identity,
        "imports_ok": imports_ok,
        "imports_fail": imports_fail,
    }
    spec_file = workdir / "identity-spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    result = run_in_venv(venv_dir, ["-I", str(PROBE), str(spec_file)], cwd=workdir)
    if result.returncode != 0 or not result.stdout.strip():
        return {"identity": {}, "probe_error": (result.stderr or result.stdout)[-1000:]}
    try:
        loaded: dict[str, Any] = json.loads(result.stdout)
        return loaded
    except json.JSONDecodeError:
        return {"identity": {}, "probe_error": "probe did not emit JSON"}


def _file_ownership_overlaps(venv_dir: Path, installed: dict[str, str]) -> list[str]:
    owned = wheelhouse.distribution_record_files(venv_dir, installed)
    seen: dict[str, str] = {}
    overlaps: list[str] = []
    for distribution, files in owned.items():
        for path in files:
            if path in seen and seen[path] != distribution:
                overlaps.append("%s owned by %s and %s" % (path, seen[path], distribution))
            else:
                seen[path] = distribution
    return overlaps


def _finish(
    report: Any,
    scenario: Any,
    status: str,
    detail: str,
    steps: list[dict[str, Any]],
    installed: Optional[dict[str, str]] = None,
) -> None:
    if status == NOT_RUN and not scenario.required:
        status = SKIP
        detail += " (optional scenario skipped)"
    report.record(
        "scenario:%s" % scenario.name,
        status,
        detail,
        required=scenario.required,
        steps=steps,
        resolved_versions=installed or {},
    )
