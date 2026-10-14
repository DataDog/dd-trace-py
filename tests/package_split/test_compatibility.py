"""compatibility gate: scenario matrix driven by crafted wheels.

The candidate wheel is supplied via --candidate (as it would be when CI
reuses the artifacts gate output), so these tests need no build backend.
"""

from __future__ import annotations

from pathlib import Path

from package_split.evidence import FAIL
from package_split.evidence import NOT_RUN
from package_split.evidence import PASS
from package_split.evidence import SKIP
from package_split.gates import compatibility

from ._support import Args
from ._support import craft_wheel
from ._support import make_report
from ._support import make_tree
from ._support import package_files


def run_compatibility(tmp_path: Path, matrix_text: str):
    from package_split.manifest import load_manifest

    wheels = tmp_path / "wheels"
    candidate = craft_wheel(
        wheels,
        "sample",
        "2.0.0",
        package_files(
            "sample",
            **{
                "__init__.py": "__version__ = '2.0.0'\n",
                "thing.py": "value = 1\n",
                "native_feature.py": "raise ImportError('native feature requires the native adapter')\n",
            },
        ),
    )
    matrix = tmp_path / "matrix.toml"
    matrix.write_text(matrix_text, encoding="utf-8")
    manifest_path = make_tree(tmp_path / "src", "sample", {"__init__.py": ""})
    manifest = load_manifest(manifest_path)
    report = make_report("compatibility", manifest_path)
    compatibility.run_gate(manifest, Args(matrix=matrix, candidate=candidate), report)
    return report


def test_supported_combination_passes(tmp_path):
    report = run_compatibility(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "minimum-deps"
installs = [{ artifact = "candidate" }]

[scenario.expectations]
imports_ok = ["sample.thing"]
imports_fail = ["sample.native_feature"]
commands = ["import sample.thing"]
""",
    )
    assert report.status == PASS, report.summary()
    scenario = [c for c in report.checks if c.name == "scenario:minimum-deps"]
    assert scenario and scenario[0].status == PASS


def test_unsupported_combination_must_be_rejected(tmp_path):
    # The plan requires unsupported combinations to be rejected clearly; an
    # import that should fail but succeeds fails the gate.
    report = run_compatibility(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "bad-combo"
installs = [{ artifact = "candidate" }]

[scenario.expectations]
imports_fail = ["sample.thing"]
""",
    )
    assert report.status == FAIL
    scenario = [c for c in report.checks if c.name == "scenario:bad-combo"]
    assert scenario and scenario[0].status == FAIL
    assert "must be rejected clearly" in scenario[0].detail


def test_behavioral_command_failure_fails(tmp_path):
    report = run_compatibility(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "e2e"
installs = [{ artifact = "candidate" }]

[scenario.expectations]
commands = ["import sys; sys.exit(7)"]
""",
    )
    assert report.status == FAIL
    scenario = [c for c in report.checks if c.name == "scenario:e2e"]
    assert scenario and scenario[0].status == FAIL


def test_missing_artifact_required_scenario_fails(tmp_path):
    report = run_compatibility(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "released-combo"
installs = [{ artifact = "missing-wheel.whl" }]
""",
    )
    assert report.status == FAIL
    scenario = [c for c in report.checks if c.name == "scenario:released-combo"]
    assert scenario and scenario[0].status == NOT_RUN


def test_missing_artifact_optional_scenario_skips_but_is_visible(tmp_path):
    report = run_compatibility(
        tmp_path,
        """\
schema_version = 1

[[scenario]]
name = "optional-combo"
required = false
installs = [{ artifact = "missing-wheel.whl" }]
""",
    )
    assert report.status == PASS
    scenario = [c for c in report.checks if c.name == "scenario:optional-combo"]
    assert scenario and scenario[0].status == SKIP


def test_missing_matrix_never_passes(tmp_path):
    from package_split.manifest import load_manifest

    manifest_path = make_tree(tmp_path, "sample", {"__init__.py": ""})
    manifest = load_manifest(manifest_path)
    report = make_report("compatibility", manifest_path)
    compatibility.run_gate(manifest, Args(matrix=None), report)
    assert report.status == FAIL
    assert any(c.status == NOT_RUN for c in report.checks)
