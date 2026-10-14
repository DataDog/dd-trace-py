"""compatibility gate: exact candidate/released version-combination matrix.

Pass criteria (docs/package-split/plan.md):

- every scenario in the matrix installs an exact, recorded set of artifacts
  (candidate wheel built from this checkout plus released wheels or pinned
  requirements) into a fresh venv and runs its checks;
- ``imports_ok`` targets import cleanly, ``imports_fail`` targets raise
  (unsupported combinations must be rejected clearly, not silently);
- behavioral commands run inside the scenario venv and pass;
- a required scenario that could not execute (missing artifact or
  prerequisite) fails the gate; optional scenarios are recorded as skipped.
"""

from __future__ import annotations

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
from ..matrices import scenario_commands
from ..venvutil import VenvError
from ..venvutil import create_venv
from ..venvutil import installed_distributions
from ..venvutil import pip_install
from ..venvutil import run_in_venv


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
        matrix = parse_matrix(matrix_path, "compatibility")
    except MatrixError as exc:
        report.record("matrix", FAIL, str(exc))
        return
    report.record(
        "matrix",
        PASS,
        "%d scenario(s) parsed from %s" % (len(matrix.scenarios), matrix_path),
    )

    workdir = Path(tempfile.mkdtemp(prefix="package-split-compat-"))

    # The candidate wheel is built once from this checkout and reused, unless
    # an already-built candidate is supplied via --candidate.
    candidate_path = getattr(args, "candidate", None)
    if candidate_path:
        candidate: dict[str, Any] = {
            "path": Path(candidate_path),
            "sha256": wheelhouse.sha256_file(Path(candidate_path)),
        }
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
    report.record(
        "candidate-build",
        PASS,
        candidate["path"].name,
        candidate=candidate,
    )

    for scenario in matrix.scenarios:
        _run_scenario(report, scenario, candidate, matrix_path.parent, workdir)


def _resolve_requirement(install: dict[str, str], candidate: dict[str, Any], matrix_dir: Path) -> dict[str, Any]:
    """Resolve one install entry to a pip requirement string, or an error."""
    if "artifact" in install:
        value = install["artifact"]
        if value == "candidate":
            return {"requirement": str(candidate["path"]), "kind": "candidate"}
        path = Path(value)
        if not path.is_absolute():
            # Relative paths resolve against the matrix file's directory.
            path = matrix_dir / value
        if path.is_file():
            return {
                "requirement": str(path),
                "kind": "artifact",
                "sha256": wheelhouse.sha256_file(path),
            }
        return {"error": "artifact %r does not exist" % value, "kind": "artifact"}
    return {"requirement": install["requirement"], "kind": "requirement"}


def _run_scenario(
    report: Any,
    scenario: Any,
    candidate: dict[str, Any],
    matrix_dir: Path,
    workdir: Path,
) -> None:
    venv_dir = workdir / ("venv-" + scenario.name.replace("/", "-"))

    requirements: list[str] = []
    details: list[dict[str, Any]] = []
    for install in scenario.installs:
        resolved = _resolve_requirement(install, candidate, matrix_dir)
        if "error" in resolved:
            _finish_scenario(
                report,
                scenario,
                NOT_RUN,
                "unresolvable install: %s" % resolved["error"],
                details,
            )
            return
        requirements.append(resolved["requirement"])
        details.append(resolved)

    try:
        create_venv(venv_dir)
        pip_install(venv_dir, requirements, find_links=[matrix_dir])
    except VenvError as exc:
        _finish_scenario(report, scenario, NOT_RUN, "environment setup failed: %s" % str(exc)[:1000], details)
        return

    installed = installed_distributions(venv_dir)
    failures: list[str] = []

    for target in scenario.expectations.get("imports_ok", []):
        result = run_in_venv(venv_dir, ["-c", "import %s" % target], cwd=workdir)
        if result.returncode != 0:
            failures.append("expected import of %s to succeed: %s" % (target, result.stderr[-500:]))
    for target in scenario.expectations.get("imports_fail", []):
        result = run_in_venv(venv_dir, ["-c", "import %s" % target], cwd=workdir)
        if result.returncode == 0:
            failures.append(
                "expected import of %s to fail (unsupported combinations must be rejected clearly)" % target
            )
    for command in scenario_commands(scenario.expectations.get("commands", [])):
        result = run_in_venv(venv_dir, command, cwd=workdir)
        if result.returncode != 0:
            failures.append("command %r failed: %s" % (" ".join(command), (result.stderr or result.stdout)[-500:]))

    if failures:
        _finish_scenario(report, scenario, FAIL, "; ".join(failures)[:2000], details, installed)
    else:
        _finish_scenario(
            report,
            scenario,
            PASS,
            "%d install(s), %d check(s) passed" % (len(requirements), len(scenario.expectations)),
            details,
            installed,
        )


def _finish_scenario(
    report: Any,
    scenario: Any,
    status: str,
    detail: str,
    details: list[dict[str, Any]],
    installed: Optional[dict[str, str]] = None,
) -> None:
    # not_run on an optional scenario is a visible skip; on a required
    # scenario it stays not_run and fails the gate.
    if status == NOT_RUN and not scenario.required:
        status = SKIP
        detail += " (optional scenario skipped)"
    report.record(
        "scenario:%s" % scenario.name,
        status,
        detail,
        required=scenario.required,
        installs=details,
        resolved_versions=installed or {},
    )
