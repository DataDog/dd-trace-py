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
                    "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED": "1",
                },
            )
        ],
    )


@pytest.mark.parametrize("mode", ["itr", "full", "collect", "testmon"])
def test_tia_dependencies_and_command(runner_module, runner, environment, monkeypatch, mode):
    monkeypatch.setenv("DD_LLMOBS_TIA_MODE", mode)
    prepared = runner._prepare_environment(environment, Path("wheel.whl"))
    assert ("pytest-testmon==2.1.3" in prepared.requirements_contents) == (mode in ("collect", "testmon"))
    commands = []

    def execute(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner_module.subprocess, "run", execute)
    assert runner._run_environments([environment], {}, ["--ddtrace"], True)
    command = commands[0]
    assert f"DD_CIVISIBILITY_ITR_ENABLED={'1' if mode == 'itr' else '0'}" in command
    assert "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED=0" in command
    assert "--ddtrace --no-cov -n 0 -p scripts.llmobs_tia" in command[-1]
    assert ("--testmon" in command[-1]) == (mode in ("collect", "testmon"))
    assert ("--testmon-noselect" in command[-1]) == (mode == "collect")
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
    for mode in ("collect", "testmon", "collect"):
        monkeypatch.setenv("DD_LLMOBS_TIA_MODE", mode)
        assert not runner._run_environments([environment], {}, ["--ddtrace"], True)
    assert [exists for _, exists in databases] == [False, True, False]
    assert len({path for path, _ in databases}) == 1
    Path("lock.txt").write_text("pytest==8.4.0\n")
    monkeypatch.setenv("DD_LLMOBS_TIA_MODE", "testmon")
    assert not runner._run_environments([environment], {}, ["--ddtrace"], True)
    assert databases[-1][0] != databases[0][0]
    assert databases[-1][1] is False
    for report in (runner.root / ".tia/reports").glob("*.json"):
        metrics = json.loads(report.read_text())
        assert metrics["exit_code"] == 1
        assert metrics["database_bytes"] == len(b"coverage")


def test_tia_does_not_change_other_suites_or_local_runs(runner, environment, monkeypatch):
    monkeypatch.setenv("DD_LLMOBS_TIA_MODE", "testmon")
    environment.suite = "tracer"
    assert runner._tia_mode(environment) == ""
    environment.suite = "llmobs::llmobs"
    runner.in_ci = False
    assert runner._tia_mode(environment) == ""


@pytest.mark.parametrize(
    "mode,reused,exit_code,failed,expected",
    [
        ("testmon", "1", 5, 0, 0),
        ("testmon", "0", 5, 0, 5),
        ("collect", "1", 5, 0, 5),
        ("testmon", "1", 2, 1, 2),
        ("testmon", "1", 1, 1, 1),
    ],
)
def test_empty_selection_does_not_hide_failures(monkeypatch, tmp_path, mode, reused, exit_code, failed, expected):
    report = tmp_path / "report.json"
    monkeypatch.setenv("DD_LLMOBS_TIA_MODE", mode)
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
