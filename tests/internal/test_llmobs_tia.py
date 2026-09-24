import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest import mock

import pytest

from scripts import llmobs_tia


@pytest.fixture
def runner_module():
    # Loading the runner should not require generator-only dependencies in test venvs.
    suitespec = ModuleType("tests.suitespec")
    suitespec.TestEnvironment = object
    suitespec.TestRun = object
    suitespec.get_patterns = lambda *args, **kwargs: {}
    suitespec.get_suites = lambda *args, **kwargs: {}
    suitespec.get_test_environments = lambda *args, **kwargs: {}
    path = Path(__file__).resolve().parents[2] / "scripts/run-tests"
    loader = importlib.machinery.SourceFileLoader("tia_test_runner", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"tests.suitespec": suitespec, spec.name: module}):
        loader.exec_module(module)
    return module


@pytest.fixture
def runner(runner_module, tmp_path, monkeypatch):
    runner = runner_module.TestRunner()
    runner.in_ci = True
    runner.root = tmp_path
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lock.txt").write_text("pytest==8.3.5\ncoverage==7.8.0\n")
    (tmp_path / "wheel.whl").write_bytes(b"wheel")
    monkeypatch.setattr(runner, "_prebuilt_ddtrace_wheel", lambda python: Path("wheel.whl"))
    monkeypatch.setattr(runner, "_build_environment", lambda *args, **kwargs: True)
    return runner


@pytest.fixture
def environment():
    return SimpleNamespace(
        suite="llmobs::llmobs",
        integration_name="llmobs",
        python="3.12",
        hash="abc123",
        lockfile=Path("lock.txt"),
        runs=[
            SimpleNamespace(
                command="pytest -n auto --dist=worksteal {cmdargs} tests/llmobs",
                environment={
                    "DD_TRACE_PY_ENABLE_ITR_TEST_SKIPPING_FOR_JOB": "true",
                    "DD_CIVISIBILITY_ITR_ENABLED": "1",
                    "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED": "1",
                    "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "1",
                },
            )
        ],
    )


def test_testmon_replaces_itr_by_default(runner_module, runner, environment, monkeypatch):
    prepared = runner._prepare_environment(environment, Path("wheel.whl"))
    assert "pytest-testmon==2.1.3" in prepared.requirements_contents
    commands = []

    def execute(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner_module.subprocess, "run", execute)
    assert runner._run_environments([environment], {}, ["--ddtrace"], True)
    command = commands[0]
    assert "DD_CIVISIBILITY_ITR_ENABLED=0" in command
    assert "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED=0" in command
    assert "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED=1" in command
    assert "pytest -n auto --dist=worksteal" in command[-1]
    assert "--ddtrace --no-cov -p scripts.llmobs_tia" in command[-1]
    assert "--testmon" in command[-1]
    assert "-n 0" not in command[-1]
    report = json.loads(next((runner.root / ".tia/reports").glob("*.json")).read_text())
    assert report["database_reused"] is False
    assert report["exit_code"] == 0


def test_database_reuse_invalidation_and_failures(runner_module, runner, environment, monkeypatch):
    databases = []

    def execute(command, **kwargs):
        assignments = dict(arg.split("=", 1) for arg in command[1:-3])
        database = Path(assignments["TESTMON_DATAFILE"])
        databases.append((database, database.exists()))
        database.write_bytes(b"coverage")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(runner_module.subprocess, "run", execute)
    for _ in range(2):
        assert not runner._run_environments([environment], {}, ["--ddtrace"], True)
    assert [exists for _, exists in databases] == [False, True]
    assert len({path for path, _ in databases}) == 1
    Path("lock.txt").write_text("pytest==8.4.0\n")
    assert not runner._run_environments([environment], {}, ["--ddtrace"], True)
    assert databases[-1][0] != databases[0][0]
    assert databases[-1][1] is False
    for report in (runner.root / ".tia/reports").glob("*.json"):
        metrics = json.loads(report.read_text())
        assert metrics["exit_code"] == 1
        assert metrics["database_bytes"] == len(b"coverage")


def test_tia_does_not_change_other_suites_or_local_runs(runner, environment):
    environment.suite = "tracer"
    assert not runner._uses_testmon(environment)
    prepared = runner._prepare_environment(environment, Path("wheel.whl"))
    assert "pytest-testmon" not in prepared.requirements_contents
    environment.suite = "llmobs::llmobs"
    runner.in_ci = False
    assert not runner._uses_testmon(environment)


@pytest.mark.parametrize(
    "reused,exit_code,failed,expected",
    [
        ("1", 5, 0, 0),
        ("0", 5, 0, 5),
        ("1", 2, 1, 2),
        ("1", 1, 1, 1),
    ],
)
def test_empty_selection_does_not_hide_failures(monkeypatch, tmp_path, reused, exit_code, failed, expected):
    report = tmp_path / "report.json"
    monkeypatch.setenv("DD_LLMOBS_TIA_DATABASE_REUSED", reused)
    monkeypatch.setenv("DD_LLMOBS_TIA_REPORT", str(report))
    monkeypatch.setattr(llmobs_tia, "version", lambda package: "test-version")
    measurements = llmobs_tia.Measurements()
    session = SimpleNamespace(testscollected=0, testsfailed=failed, exitstatus=exit_code)
    hook = measurements.pytest_sessionfinish(session, exit_code)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
    assert session.exitstatus == expected
    assert json.loads(report.read_text())["pytest_exit_code"] == exit_code


def test_xdist_controller_reports_worker_selection(monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    monkeypatch.setenv("DD_LLMOBS_TIA_REPORT", str(report))
    monkeypatch.setattr(llmobs_tia, "version", lambda package: "test-version")
    measurements = llmobs_tia.Measurements()
    for _ in range(2):
        measurements.pytest_xdist_node_collection_finished(None, ["test_a", "test_b"])
    measurements.pytest_runtest_logreport(SimpleNamespace(when="call", outcome="passed", failed=False, skipped=False))
    hook = measurements.pytest_sessionfinish(SimpleNamespace(testscollected=2, testsfailed=0, exitstatus=0), 0)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
    metrics = json.loads(report.read_text())
    assert metrics["selected_tests"] == 2
    assert metrics["collection_seconds"] is None
    assert metrics["deselected_tests"] is None
    assert metrics["outcomes"] == {"call_passed": 1}


def test_xdist_workers_do_not_write_shared_report():
    manager = mock.Mock()
    llmobs_tia.pytest_configure(SimpleNamespace(workerinput={}, pluginmanager=manager))
    manager.register.assert_not_called()
