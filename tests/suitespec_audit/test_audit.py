import json

import pytest
from ruamel.yaml import YAML

from scripts import audit_suite_dependencies as audit
from tests import suitespec


@pytest.fixture
def coverage_report(tmp_path):
    def write(files, name="coverage.json"):
        report = tmp_path / name
        report.write_text(json.dumps({"files": files}))
        return report

    return write


@pytest.fixture
def suite(monkeypatch):
    monkeypatch.setattr(
        suitespec,
        "SUITESPEC",
        {
            "components": {
                "$harness": ["ddtrace/harness.py"],
                "core": ["ddtrace/internal/*"],
                "telemetry": ["ddtrace/internal/telemetry/*"],
                "telemetry_alias": ["ddtrace/internal/telemetry/*"],
                "exact": ["ddtrace/internal/telemetry/exact.py"],
                "declared": ["ddtrace/declared/*"],
            },
            "suites": {"example::suite": {"paths": ["@declared", "ddtrace/tested/*"]}},
        },
    )
    caches = (suitespec.get_patterns, suitespec._owners, suitespec._component_matchers, suitespec._imported_components)
    for cache in caches:
        cache.cache_clear()
    yield "example::suite"
    for cache in caches:
        cache.cache_clear()


@pytest.mark.parametrize(
    "filename, roots, expected",
    [
        ("ddtrace/internal/foo.py", (), "ddtrace/internal/foo.py"),
        ("./ddtrace/internal/foo.py", (), "ddtrace/internal/foo.py"),
        ("/capture/ddtrace/internal/foo.py", ("/capture",), "ddtrace/internal/foo.py"),
        ("/capture/ddtrace/internal/foo.py", (), None),
        ("/capture-other/ddtrace/foo.py", ("/capture",), None),
        (".venv/lib/site-packages/ddtrace/foo.py", (".venv/lib/site-packages",), "ddtrace/foo.py"),
        (r"C:\capture\ddtrace\foo.py", (r"C:\capture",), "ddtrace/foo.py"),
        ("tests/test_foo.py", (), None),
        ("ddtrace/../outside.py", (), None),
        ("ddtrace", (), None),
    ],
)
def test_source_path(filename, roots, expected):
    assert audit.source_path(filename, roots) == expected


def test_union_reports_only_includes_executed_sources(coverage_report):
    first = coverage_report(
        {
            "ddtrace/a.py": {"executed_lines": [1]},
            "ddtrace/not_executed.py": {"executed_lines": []},
            "tests/test_a.py": {"executed_lines": [1]},
        },
        "first.json",
    )
    second = coverage_report(
        {
            "/capture/ddtrace/a.py": {"executed_lines": [2]},
            "/capture/ddtrace/b.py": {"executed_lines": [1]},
        },
        "second.json",
    )
    assert audit.observed_sources([first, second], ("/capture",)) == {"ddtrace/a.py", "ddtrace/b.py"}


@pytest.mark.parametrize("files", [{}, {"ddtrace/a.py": {"executed_lines": []}}])
def test_empty_observations_fail(coverage_report, files):
    with pytest.raises(ValueError, match="no executed ddtrace sources"):
        audit.observed_sources([coverage_report(files)], ())


def test_partially_unmapped_report_fails(coverage_report):
    report = coverage_report(
        {
            "ddtrace/a.py": {"executed_lines": [1]},
            "/unmapped/ddtrace/b.py": {"executed_lines": [1]},
        }
    )
    with pytest.raises(ValueError, match="unmapped ddtrace source.*--source-root"):
        audit.observed_sources([report], ())


@pytest.mark.parametrize("value", [None, {}, "1", [True], [0], [-1], ["1"]])
def test_invalid_line_data_fails(coverage_report, value):
    with pytest.raises(ValueError, match="executed_lines|line number"):
        audit.observed_sources([coverage_report({"ddtrace/a.py": {"executed_lines": value}})], ())


@pytest.mark.parametrize("data", [[], {}, {"files": []}])
def test_invalid_report_fails(tmp_path, data):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="files mapping"):
        audit.observed_sources([report], ())


def test_additions_use_specific_owners_and_preserve_unowned_files(suite):
    observed = {
        "ddtrace/declared/a.py",
        "ddtrace/harness.py",
        "ddtrace/tested/a.py",
        "ddtrace/internal/telemetry/a.py",
        "ddtrace/internal/telemetry/b.py",
        "ddtrace/internal/telemetry/exact.py",
        "ddtrace/unowned.py",
    }
    additions, missing = audit.dependency_additions(suite, observed)
    assert additions == ["@exact", "@telemetry", "@telemetry_alias", "ddtrace/unowned.py"]
    assert missing == [
        "ddtrace/internal/telemetry/a.py",
        "ddtrace/internal/telemetry/b.py",
        "ddtrace/internal/telemetry/exact.py",
        "ddtrace/unowned.py",
    ]
    assert suitespec.get_suites()[suite]["paths"] == ["@declared", "ddtrace/tested/*"]


def test_static_discovery_counts_as_an_existing_trigger(monkeypatch, suite):
    monkeypatch.setattr(suitespec, "_imported_components", lambda _: frozenset({"telemetry"}))
    assert audit.dependency_additions(suite, {"ddtrace/internal/telemetry/a.py"}) == ([], [])


def test_unknown_suite_fails():
    with pytest.raises(ValueError, match="Unknown suite"):
        audit.dependency_additions("does-not-exist", {"ddtrace/a.py"})


@pytest.mark.parametrize("check, expected", [(False, 0), (True, 1)])
def test_cli_emits_yaml_additions_and_reports_missing_files(suite, coverage_report, capsys, check, expected):
    report = coverage_report({"ddtrace/unowned.py": {"executed_lines": [1]}})
    args = ["--suite", suite, "--coverage", str(report)] + (["--check"] if check else [])
    assert audit.main(args) == expected
    output = capsys.readouterr()
    assert YAML(typ="safe").load(output.out) == {"suites": {suite: {"paths": ["ddtrace/unowned.py"]}}}
    assert "ddtrace/unowned.py" in output.err


def test_cli_check_passes_when_observations_are_covered(suite, coverage_report, capsys):
    report = coverage_report({"ddtrace/declared/a.py": {"executed_lines": [1]}})
    assert audit.main(["--suite", suite, "--coverage", str(report), "--check"]) == 0
    assert YAML(typ="safe").load(capsys.readouterr().out) == {"suites": {suite: {"paths": []}}}


def test_cli_rejects_unknown_suite_before_reading_reports(capsys):
    with pytest.raises(SystemExit) as exc:
        audit.main(["--suite", "does-not-exist", "--coverage", "missing.json"])
    assert exc.value.code == 2
    assert "Unknown suite" in capsys.readouterr().err


def test_cli_reports_invalid_json(suite, tmp_path, capsys):
    report = tmp_path / "invalid.json"
    report.write_text("not json")
    with pytest.raises(SystemExit) as exc:
        audit.main(["--suite", suite, "--coverage", str(report)])
    assert exc.value.code == 2
    assert "Expecting value" in capsys.readouterr().err
