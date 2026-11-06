"""contracts gate: API snapshot comparison and behavioral fixtures."""

from __future__ import annotations

from pathlib import Path
import sys

from package_split.evidence import FAIL
from package_split.evidence import NOT_RUN
from package_split.evidence import PASS
from package_split.gates import contracts

from ._support import Args
from ._support import craft_wheel
from ._support import make_report
from ._support import make_tree
from ._support import package_files


def _make_wheels(tmp_path: Path, baseline_mod: str, candidate_mod: str):
    baseline = craft_wheel(
        tmp_path,
        "sample",
        "1.0.0",
        package_files("sample", **{"__init__.py": baseline_mod}),
    )
    candidate = craft_wheel(
        tmp_path,
        "sample",
        "1.1.0",
        package_files("sample", **{"__init__.py": candidate_mod}),
    )
    return baseline, candidate


def run_contracts(tmp_path: Path, baseline: Path, candidate: Path, fixtures=None):
    from package_split.manifest import load_manifest

    manifest_path = make_tree(
        tmp_path / "manifest-root",
        "sample",
        {"__init__.py": ""},
        manifest_kwargs={"exports": ["helper", "gone_soon"], "fixtures": fixtures},
    )
    manifest = load_manifest(manifest_path)
    report = make_report("contracts", manifest_path)
    contracts.run_gate(manifest, Args(baseline=baseline, candidate=candidate), report)
    return report


BASELINE = "def helper(a, b=1):\n    'doc'\n    return a + b\nclass gone_soon:\n    pass\n"


def test_compatible_candidate_passes(tmp_path):
    baseline, candidate = _make_wheels(tmp_path, BASELINE, BASELINE)
    report = run_contracts(tmp_path, baseline, candidate)
    assert report.status == PASS, report.summary()
    assert any(c.name == "api-compatibility" and c.status == PASS for c in report.checks)


def test_incompatible_signature_change_fails(tmp_path):
    changed = BASELINE.replace("def helper(a, b=1):", "def helper(a, b, c):")
    baseline, candidate = _make_wheels(tmp_path, BASELINE, changed)
    report = run_contracts(tmp_path, baseline, candidate)
    assert report.status == FAIL
    api = [c for c in report.checks if c.name == "api-compatibility"]
    assert api and api[0].status == FAIL
    assert any("signature" in d["change"] for d in api[0].data["differences"])


def test_default_value_change_fails(tmp_path):
    changed = BASELINE.replace("b=1", "b=2")
    baseline, candidate = _make_wheels(tmp_path, BASELINE, changed)
    report = run_contracts(tmp_path, baseline, candidate)
    assert report.status == FAIL
    assert any(
        "defaults" in d["change"] for c in report.checks if c.name == "api-compatibility" for d in c.data["differences"]
    )


def test_removed_export_fails(tmp_path):
    changed = BASELINE.replace("class gone_soon:\n    pass\n", "")
    baseline, candidate = _make_wheels(tmp_path, BASELINE, changed)
    report = run_contracts(tmp_path, baseline, candidate)
    assert report.status == FAIL
    assert any(
        "removed from candidate" in d["change"]
        for c in report.checks
        if c.name == "api-compatibility"
        for d in c.data["differences"]
    )


def test_missing_baseline_never_passes(tmp_path):
    _, candidate = _make_wheels(tmp_path, BASELINE, BASELINE)
    report = run_contracts(tmp_path, tmp_path / "does-not-exist.whl", candidate)
    assert report.status == FAIL
    assert any(c.name == "baseline" and c.status == NOT_RUN for c in report.checks)


def test_behavioral_fixture_failure_fails(tmp_path):
    baseline, candidate = _make_wheels(tmp_path, BASELINE, BASELINE)
    report = run_contracts(
        tmp_path,
        baseline,
        candidate,
        fixtures=[[sys.executable, "-c", "import sys; sys.exit(3)"]],
    )
    assert report.status == FAIL
    fixtures = [c for c in report.checks if c.name == "behavioral-fixtures"]
    assert fixtures and fixtures[0].status == FAIL


def test_behavioral_fixture_passing(tmp_path):
    baseline, candidate = _make_wheels(tmp_path, BASELINE, BASELINE)
    report = run_contracts(
        tmp_path,
        baseline,
        candidate,
        fixtures=[[sys.executable, "-c", "import sample; assert sample.helper(1) == 2"]],
    )
    assert report.status == PASS, report.summary()
