"""Matrix files for the compatibility and migration gates.

Both gates are driven by a TOML scenario matrix (schema v1). Scenarios have
an explicit ``required`` flag (default true): a required scenario that could
not execute (missing artifact, missing prerequisite) is ``not_run`` and fails
the gate; an optional scenario is recorded as skipped with its reason.

compatibility matrix::

    schema_version = 1
    [[scenario]]
    name = "minimum-deps"
    required = true
    installs = [{ artifact = "candidate" }, { requirement = "bytecode==0.13.0" }]
    [scenario.expectations]
    imports_ok = ["ddtrace_apm.thing"]
    imports_fail = ["ddtrace_apm.native_feature"]
    # Commands run inside the scenario venv with its interpreter; each
    # entry is either a string (run as ``python -c <string>``) or an argv
    # vector appended to the interpreter (e.g. ["-m", "pytest", ...]).
    commands = ["import ddtrace_apm.thing"]

migration matrix::

    schema_version = 1
    [[scenario]]
    name = "coexistence"
    required = true
    [[scenario.step]]
    action = "install"          # install | uninstall
    artifact = "candidate"      # or a path / requirement
    [[scenario.step]]
    action = "uninstall"
    distribution = "ddtrace"
    [scenario.checks]
    file_ownership = true
    imports_ok = ["ddtrace_apm"]
    [scenario.checks.identity]
    bridge = "bridge_mod:Provider"
    expect_module = "ddtrace_apm.provider"
    [scenario.checks.entry_points]
    group = "pytest11"
    count = 1
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Optional

from . import MATRIX_SCHEMA_VERSION


class MatrixError(Exception):
    pass


@dataclass
class Step:
    action: str  # "install" | "uninstall"
    artifact: Optional[str] = None  # "candidate", a path, or a requirement
    distribution: Optional[str] = None  # for uninstall


@dataclass
class Scenario:
    name: str
    required: bool = True
    installs: list[dict[str, str]] = field(default_factory=list)  # compatibility
    steps: list[Step] = field(default_factory=list)  # migration
    expectations: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)


@dataclass
class Matrix:
    path: Path
    kind: str  # "compatibility" | "migration"
    scenarios: list[Scenario]


def parse_matrix(path: Path, kind: str) -> Matrix:
    from ._toml import tomllib

    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    issues: list[str] = []

    version = data.get("schema_version")
    if version != MATRIX_SCHEMA_VERSION:
        issues.append("schema_version must be %d, got %r" % (MATRIX_SCHEMA_VERSION, version))

    raw_scenarios = data.get("scenario", [])
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        issues.append("at least one [[scenario]] is required")

    scenarios: list[Scenario] = []
    for i, raw in enumerate(raw_scenarios):
        if not isinstance(raw, dict):
            issues.append("[[scenario]] #%d must be a table" % i)
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            issues.append("[[scenario]] #%d: missing name" % i)
            continue
        scenario = Scenario(
            name=name,
            required=bool(raw.get("required", True)),
            expectations=dict(raw.get("expectations", {})),
            checks=dict(raw.get("checks", {})),
        )
        if kind == "compatibility":
            installs = raw.get("installs", [])
            if not isinstance(installs, list):
                issues.append("scenario %r: installs must be an array" % name)
            else:
                for j, install in enumerate(installs):
                    if not isinstance(install, dict) or not (("artifact" in install) ^ ("requirement" in install)):
                        issues.append(
                            "scenario %r: installs[%d] must have exactly one of 'artifact' or 'requirement'" % (name, j)
                        )
                scenario.installs = installs
        else:
            # ``[[scenario.step]]`` in TOML yields the key "step" on each
            # scenario table; an inline ``steps = [{...}]`` yields "steps".
            # Accept both spellings.
            steps = raw.get("step", raw.get("steps", []))
            if not isinstance(steps, list):
                issues.append("scenario %r: steps must be an array" % name)
            else:
                for j, step in enumerate(steps):
                    if not isinstance(step, dict) or step.get("action") not in ("install", "uninstall"):
                        issues.append("scenario %r: steps[%d] must be install/uninstall" % (name, j))
                        continue
                    if step["action"] == "install" and "artifact" not in step:
                        issues.append("scenario %r: steps[%d] install needs 'artifact'" % (name, j))
                    if step["action"] == "uninstall" and "distribution" not in step:
                        issues.append("scenario %r: steps[%d] uninstall needs 'distribution'" % (name, j))
                    scenario.steps.append(
                        Step(
                            action=step["action"],
                            artifact=step.get("artifact"),
                            distribution=step.get("distribution"),
                        )
                    )
        scenarios.append(scenario)

    if issues:
        raise MatrixError("invalid matrix %s:\n  - %s" % (path, "\n  - ".join(issues)))
    return Matrix(path=Path(path), kind=kind, scenarios=scenarios)


def scenario_commands(entry: Any) -> list[list[str]]:
    """Normalize a command list entry.

    Each command is either a string (executed as ``<venv python> -c <string>``)
    or an argv vector appended to the scenario venv's interpreter.
    """
    commands: list[list[str]] = []
    if not entry:
        return commands
    if not isinstance(entry, list):
        raise MatrixError("commands must be an array, got %r" % (entry,))
    for command in entry:
        if isinstance(command, str):
            commands.append(["-c", command])
        elif isinstance(command, list) and all(isinstance(part, str) for part in command):
            commands.append(command)
        else:
            raise MatrixError("each command must be a string or an array of parts: %r" % (command,))
    return commands
