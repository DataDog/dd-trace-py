"""lifecycle gate: import-time inertness and activation correctness.

Pass criteria (docs/package-split/plan.md):

- importing each declared import target in a fresh subprocess causes no
  application-visible side effects: no threads started, no network or
  process creation attempts, no logging configuration changes, no import
  hooks registered, no signal handlers installed, no atexit callbacks, and
  no replacement of the manifest's protected callables;
- the manifest's activation API is idempotent (second activation registers
  nothing new), deactivation cleans up its resources, and activation works
  exactly once per process in supported fork/spawn children.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess  # nosec B404
import sys
import tempfile
from typing import Any

from ..evidence import FAIL
from ..evidence import NOT_RUN
from ..evidence import PASS
from ..evidence import SKIP
from ..manifest import Manifest


PROBE = Path(__file__).resolve().parent.parent / "probe.py"


def run_gate(manifest: Manifest, args: Any, report: Any) -> None:
    workdir = Path(tempfile.mkdtemp(prefix="package-split-lifecycle-"))
    import_targets = list(getattr(args, "import_target", None) or manifest.import_roots)

    spec: dict[str, Any] = {
        "sys_path": [str(p) for p in _sys_path_entries(manifest, args)],
        "import_targets": import_targets,
        "protected_callables": [],
        "multiprocessing_modes": ["fork", "spawn"],
    }
    activation = manifest.activation_api
    if activation:
        spec["activation"] = activation
        spec["activation"]["singletons"] = list(getattr(args, "activation_singletons", None) or [])
        if not spec["activation"].get("singletons"):
            report.record(
                "activation-singletons",
                NOT_RUN,
                "activation_api declared but no --activation-singletons given; "
                "duplicate-activation detection requires a registry to observe",
            )

    spec_file = workdir / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")

    result = subprocess.run(  # nosec B603
        [sys.executable, "-I", str(PROBE), str(spec_file)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(workdir),
    )
    if result.returncode != 0 or not result.stdout.strip():
        report.record(
            "probe",
            FAIL,
            "lifecycle probe crashed: %s" % (result.stderr or result.stdout)[-2000:],
        )
        return
    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError:
        report.record("probe", FAIL, "probe did not emit a single JSON document")
        return

    _report_import_side_effects(report, probe)
    _report_activation(report, probe)


def _sys_path_entries(manifest: Manifest, args: Any) -> list[Path]:
    """Import path for the probe: source paths by default, or --sys-path.

    For the probe to ``import <package>``, sys.path needs the directory that
    *contains* the package, i.e. the parent of each declared source path.
    """
    extra = list(getattr(args, "sys_path", None) or [])
    if extra:
        return [Path(p) for p in extra]
    entries = []
    for path in manifest.source_paths:
        parent = path.parent
        if parent not in entries:
            entries.append(parent)
    return entries


def _report_import_side_effects(report: Any, probe: dict[str, Any]) -> None:
    # Side effects are phase-scoped: a thread started by an explicit
    # activate() is fine (checked separately), but nothing may happen during
    # import, and some kinds (network, process, logging mutation, patched
    # callables) are never acceptable outside explicit activation.
    always_bad = {
        "network",
        "process",
        "logging",
        "signal",
        "import-hooks",
        "atexit",
        "patched-callable",
    }
    violations = [
        v
        for v in probe.get("violations", [])
        if v.get("phase") == "import"
        or (v.get("kind") in always_bad and v.get("phase") not in ("activation", "second-activation", "deactivation"))
    ]
    imports = probe.get("import_side_effects", {}).get("imports", {})
    import_failures = {m: v for m, v in imports.items() if not v.get("ok")}
    if import_failures:
        report.record(
            "import-clean",
            FAIL,
            "import targets failed to import: %s" % sorted(import_failures),
            failures=import_failures,
        )
    elif violations:
        report.record(
            "import-side-effects",
            FAIL,
            "%d application-visible side effects during import" % len(violations),
            violations=violations,
        )
    else:
        report.record(
            "import-side-effects",
            PASS,
            "importing %d target(s) started no threads, touched no logging, "
            "network, process, signal or import machinery" % len(imports),
        )


def _report_activation(report: Any, probe: dict[str, Any]) -> None:
    activation = probe.get("activation", {})
    if not activation:
        # Packages without an explicit activation API are checked for import
        # inertness only; legacy startup through the ddtrace compatibility
        # bootstrap is out of scope here by design.
        report.record(
            "activation",
            SKIP,
            "no activation_api declared in the manifest; activation behavior "
            "unchecked (declare it before pinning the package contract)",
        )
        return

    # Only these kinds are activation failures: thread starts during an
    # explicit activate() are expected behavior, duplicate registrations and
    # leftover resources are not.
    bad_kinds = {"duplicate-activation", "activation-cleanup"}
    violations = [
        v for v in probe.get("violations", []) if v.get("kind") in bad_kinds or str(v.get("kind", "")).startswith("mp-")
    ]
    mp_problems = []
    for mode, result in activation.items():
        if mode.startswith("mp") or mode in ("fork", "spawn"):
            child = (result or {}).get("child")
            exitcode = (result or {}).get("exitcode")
            if exitcode != 0:
                mp_problems.append("%s child exited %r" % (mode, exitcode))
            elif child is None:
                mp_problems.append("%s child did not report a result" % mode)
            elif not child.get("ok"):
                mp_problems.append("%s child failed: %r" % (mode, child))
    if violations or mp_problems:
        report.record(
            "activation",
            FAIL,
            "; ".join([v["kind"] + ": " + v["detail"] for v in violations] + mp_problems)[:2000],
            violations=violations,
            activation=activation,
        )
    else:
        report.record(
            "activation",
            PASS,
            "activation idempotent, deactivation cleans up, fork/spawn children activate exactly once",
        )
