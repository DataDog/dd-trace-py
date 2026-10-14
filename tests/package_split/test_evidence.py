"""Gate report semantics: not_run never passes, skips are visible, evidence shape."""

from __future__ import annotations

import json

from package_split.evidence import FAIL
from package_split.evidence import NOT_RUN
from package_split.evidence import PASS
from package_split.evidence import SKIP

from ._support import make_report


def test_all_passing_checks_pass(tmp_path):
    report = make_report("boundaries", tmp_path / "m.toml")
    report.record("a", PASS)
    report.record("b", PASS)
    assert report.status == PASS
    assert report.exit_code == 0


def test_missing_prerequisite_never_passes(tmp_path):
    report = make_report("contracts", tmp_path / "m.toml")
    report.record("snapshot", PASS)
    report.record("baseline", NOT_RUN, "baseline not supplied")
    assert report.status == FAIL
    assert report.exit_code == 1


def test_unexecuted_required_scenario_fails_even_with_other_passes(tmp_path):
    report = make_report("compatibility", tmp_path / "m.toml")
    report.record("scenario:minimum-deps", PASS)
    report.record("scenario:latest", NOT_RUN, "artifact missing")
    assert report.status == FAIL


def test_skip_with_reason_is_visible_but_neutral(tmp_path):
    report = make_report("artifacts", tmp_path / "m.toml")
    report.record("native-check", SKIP, "not a native-free package")
    assert report.status == PASS
    assert any(c.name == "native-check" and c.status == SKIP for c in report.checks)


def test_evidence_file_structure(tmp_path):
    report = make_report("freeze", tmp_path / "m.toml")
    report.record("source-freeze", PASS, "ok")
    evidence = report.finish(tmp_path)
    data = json.loads(evidence.read_text())
    assert data["gate"] == "freeze"
    assert data["status"] == PASS
    assert data["harness_version"] == "test"
    assert data["checks"][0]["name"] == "source-freeze"
    assert data["checks"][0]["data"] == {}
    assert "source_revision" in data
    assert "environment" in data


def test_gate_error_fails(tmp_path):
    report = make_report("boundaries", tmp_path / "m.toml")
    report.record("x", PASS)
    report.error = "ValueError: boom"
    assert report.status == FAIL
    assert "boom" in report.summary()
