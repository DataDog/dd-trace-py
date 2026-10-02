import importlib.machinery
from importlib.metadata import PackageNotFoundError
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest import mock

import pytest

from scripts import testmon_logging


@pytest.fixture
def runner_module(monkeypatch):
    suitespec = ModuleType("tests.suitespec")
    suitespec.TestEnvironment = suitespec.TestRun = object
    suitespec.get_patterns = suitespec.get_suites = suitespec.get_test_environments = lambda **kwargs: {}
    script = Path(__file__).resolve().parents[2] / "scripts/run-tests"
    loader = importlib.machinery.SourceFileLoader("tia_runner", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "tia-tests")
    original_path = list(sys.path)
    with mock.patch.dict(sys.modules, {"tests.suitespec": suitespec, spec.name: module}):
        loader.exec_module(module)
    sys.path[:] = original_path
    return module


@pytest.mark.parametrize(
    "diagnostics,exit_code,baseline_failure",
    [("off", 0, False), ("selection", 0, False), ("full", 0, False), ("full", 2, False), ("full", 0, True)],
)
def test_diagnostics_preserve_incoming_database_and_exit_code(
    runner_module,
    monkeypatch,
    tmp_path,
    diagnostics,
    exit_code,
    baseline_failure,
):
    runner = runner_module.TestRunner()
    runner.root = tmp_path
    runner.in_ci = True
    lock = tmp_path / "lock.txt"
    lock.write_text("pytest==9.0.3\n")
    environment = SimpleNamespace(
        hash="env",
        python="3.12",
        integration_name="llmobs",
        suite="llmobs::llmobs",
        lockfile=lock,
    )
    prepared = SimpleNamespace(path=Path(".cache/env"))
    run = SimpleNamespace(command="pytest -n auto {cmdargs} tests/llmobs", environment={})
    database = runner._testmon_database(environment, run)
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL").fetchone()
        connection.execute("CREATE TABLE state (value TEXT)")
        connection.execute("INSERT INTO state VALUES ('incoming')")
    monkeypatch.setenv("DD_LLMOBS_TIA_DIAGNOSTICS", diagnostics)
    phases = []
    events = []
    monkeypatch.setattr(runner_module, "_tia_log", lambda event, **fields: events.append({"event": event, **fields}))

    def execute(command, **kwargs):
        env = dict(assignment.split("=", 1) for assignment in command[1:-3])
        phase = env["DD_LLMOBS_TIA_PHASE"]
        phases.append(phase)
        with sqlite3.connect(env["TESTMON_DATAFILE"]) as connection:
            assert connection.execute("SELECT value FROM state").fetchone() == ("incoming",)
            connection.execute("UPDATE state SET value = ?", (phase,))
        selected = ["keep"] if phase in ("normal", "selection") else ["keep", "exclude"]
        failed = ["exclude"] if phase == "full_baseline" and baseline_failure else []
        Path(env["DD_LLMOBS_TIA_INVENTORY"]).write_text(
            json.dumps(
                {
                    "selected": selected,
                    "failed": failed,
                    "skipped": [],
                    "outcomes": {"call_passed": len(selected)},
                    "collection_errors": 0,
                }
            )
        )
        if phase != "normal":
            assert "--no-ddtrace" in command[-1]
            assert "--junitxml=" in command[-1]
            assert Path(env["TESTMON_DATAFILE"]) != database
        return SimpleNamespace(returncode=exit_code if phase == "normal" else int(bool(failed)))

    monkeypatch.setattr(runner_module.subprocess, "run", execute)
    assert runner._run_tia(environment, prepared, run, ["--ddtrace"], {}) == (exit_code or int(baseline_failure))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM state").fetchone() == ("normal",)
    assert len(phases) == {"off": 1, "selection": 3, "full": 5}[diagnostics]
    if diagnostics != "off":
        comparison = next(event for event in events if event["event"] == "selection_comparison")
        assert comparison["excluded"] == ["exclude"]
    if baseline_failure:
        audit = next(event for event in events if event["event"] == "correctness_audit")
        assert audit["excluded_tests_failing_full_run"] == ["exclude"]
        estimate = next(event for event in events if event["event"] == "coverage_overhead_estimate")
        assert estimate["seconds"] is None


def test_cold_start_pair_keeps_file_itr_and_testmon_commands_separate(runner_module, monkeypatch, tmp_path):
    runner = runner_module.TestRunner()
    runner.in_ci = True
    runner.root = tmp_path
    lock = tmp_path / "lock.txt"
    lock.write_text("pytest==9.0.3\n")
    environment = SimpleNamespace(
        hash="env", python="3.13", suite="llmobs::llmobs", integration_name="llmobs", lockfile=lock
    )
    run = SimpleNamespace(command="pytest -n auto --dist=worksteal {cmdargs} tests/llmobs", environment={})
    prepared = SimpleNamespace(path=Path(".cache/env"))
    assert runner._uses_testmon(environment)
    default_command = runner._test_command(environment, prepared, run, ["--ddtrace"], {})
    default_env = dict(assignment.split("=", 1) for assignment in default_command[1:-3])
    assert default_env["DD_CIVISIBILITY_ITR_ENABLED"] == "0"
    monkeypatch.setenv("DD_LLMOBS_TIA_CI_MODE", "file")
    monkeypatch.setenv("CI_COMMIT_BRANCH", "main")
    assert not runner._uses_testmon(environment)
    file_command = runner._test_command(environment, prepared, run, ["--ddtrace"], {})
    file_env = dict(assignment.split("=", 1) for assignment in file_command[1:-3])
    assert file_env["DD_CIVISIBILITY_ITR_ENABLED"] == "true"
    assert file_env["_DD_CIVISIBILITY_ITR_PREVENT_TEST_SKIPPING"] == "0"
    assert file_env["DD_TRACE_PY_ENABLE_ITR_TEST_SKIPPING_FOR_JOB"] == "true"
    assert "--testmon" not in file_command[-1]

    monkeypatch.setenv("DD_LLMOBS_TIA_CI_MODE", "testmon_cold")
    assert runner._uses_testmon(environment)
    cold_command = runner._test_command(environment, prepared, run, ["--ddtrace"], {})
    cold_env = dict(assignment.split("=", 1) for assignment in cold_command[1:-3])
    assert cold_env["DD_CIVISIBILITY_ITR_ENABLED"] == "0"
    assert "--testmon" in cold_command[-1]
    assert "--no-cov" in cold_command[-1]
    assert cold_command[-1].replace(" --no-cov -p scripts.testmon_logging --testmon", "") == file_command[-1]


def test_storage_sweep_records_only_python313_pytest_commands(runner_module, monkeypatch, tmp_path):
    runner = runner_module.TestRunner()
    runner.in_ci = True
    runner.root = tmp_path
    lock = tmp_path / "lock.txt"
    lock.write_text("pytest==9.0.3\n")
    run = SimpleNamespace(command="python -m pytest {cmdargs} tests/internal", environment={})
    environment = SimpleNamespace(
        hash="env", python="3.13", suite="internal", integration_name="internal", lockfile=lock, runs=(run,)
    )
    monkeypatch.setenv("DD_TIA_STORAGE_SWEEP", "true")
    assert runner._uses_testmon(environment)
    command = runner._test_command(environment, SimpleNamespace(path=Path(".cache/env")), run, ["--ddtrace"], {})
    forwarded = dict(assignment.split("=", 1) for assignment in command[1:-3])
    assert forwarded["DD_CIVISIBILITY_ITR_ENABLED"] == "0"
    assert "--testmon" in command[-1]

    environment.python = "3.12"
    assert not runner._uses_testmon(environment)
    environment.python = "3.13"
    environment.runs = (SimpleNamespace(command="python tests/smoke_test.py {cmdargs}"),)
    assert not runner._uses_testmon(environment)
    environment.runs = (SimpleNamespace(command="pytest tests/internal"),)
    assert not runner._uses_testmon(environment)


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_cold_start_rejects_restored_command_database(runner_module, monkeypatch, tmp_path, suffix):
    runner = runner_module.TestRunner()
    runner.root = tmp_path
    runner.in_ci = True
    lock = tmp_path / "lock.txt"
    lock.write_text("pytest==9.0.3\n")
    environment = SimpleNamespace(hash="env", python="3.13", suite="llmobs::llmobs", lockfile=lock)
    run = SimpleNamespace(command="pytest {cmdargs} tests/llmobs", environment={})
    database = runner._testmon_database(environment, run)
    database.parent.mkdir(parents=True)
    database.with_name(database.name + suffix).write_bytes(b"stale")
    monkeypatch.setenv("DD_LLMOBS_TIA_CI_MODE", "testmon_cold")
    with pytest.raises(ValueError, match="cold-start database must be absent"):
        runner._run_tia(environment, SimpleNamespace(path=Path(".cache/env")), run, ["--ddtrace"], {})


def test_configuration_logs_missing_optional_pytest_packages(monkeypatch):
    reporter = mock.Mock()
    config = SimpleNamespace(
        pluginmanager=SimpleNamespace(getplugin=lambda name: reporter),
        getoption=lambda name, default=None: default,
    )
    installed = {"pytest": "9.0.3", "pytest-testmon": "2.2.0"}

    def fake_version(name):
        if name not in installed:
            raise PackageNotFoundError(name)
        return installed[name]

    monkeypatch.setattr(testmon_logging, "version", fake_version)
    testmon_logging.TestmonLogging(config).pytest_sessionstart(None)

    event = json.loads(reporter.write_line.call_args.args[0].removeprefix("[TIA] "))
    assert event["event"] == "pytest_configuration"
    assert event["packages"] == {"pytest": "9.0.3", "pytest-testmon": "2.2.0", "coverage": None, "pytest-xdist": None}


def test_collection_without_local_items_uses_xdist_worker_ids():
    logger = testmon_logging.TestmonLogging(SimpleNamespace())
    hook = logger.pytest_collection(SimpleNamespace())
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
    assert logger.selected == set()
    logger.pytest_xdist_node_collection_finished(None, ["tests/example.py::test_works"])
    assert logger.selected == {"tests/example.py::test_works"}


def test_deselection_without_nodeid_is_counted_and_does_not_hide_known_tests():
    logger = testmon_logging.TestmonLogging(SimpleNamespace(workerinput={}))
    logger.pytest_deselected([SimpleNamespace(nodeid="before"), SimpleNamespace(), SimpleNamespace(nodeid="after")])
    logger.pytest_deselected([SimpleNamespace(nodeid="before"), SimpleNamespace()])
    assert logger.deselected == {"before", "after"}
    assert logger.deselection_notifications_without_nodeid == 2
    hook = logger.pytest_sessionfinish(None, 0)
    logger.config.workeroutput = {}
    next(hook)
    assert logger.config.workeroutput["tia"]["deselection_notifications_without_nodeid"] == 2
    with pytest.raises(StopIteration):
        next(hook)


def test_worker_results_are_deduplicated_and_only_controller_writes_inventory(monkeypatch, tmp_path):
    reporter = mock.Mock()
    config = SimpleNamespace(pluginmanager=SimpleNamespace(getplugin=lambda name: reporter))
    logger = testmon_logging.TestmonLogging(config)
    for _ in range(2):
        logger.pytest_xdist_node_collection_finished(None, ["keep"])
        node = SimpleNamespace(
            workeroutput={
                "tia": {
                    "deselected": ["exclude"],
                    "deselection_notifications_without_nodeid": 2,
                    "collection_seconds": 1.0,
                }
            },
            gateway=SimpleNamespace(id="gw0"),
        )
        logger.pytest_testnodedown(node, None)
    output = tmp_path / "inventory.json"
    monkeypatch.setenv("DD_LLMOBS_TIA_INVENTORY", str(output))
    hook = logger.pytest_sessionfinish(None, 0)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
    data = json.loads(output.read_text())
    assert data["selected"] == ["keep"]
    assert data["deselected"] == ["exclude"]
    assert data["deselection_notifications_without_nodeid"] == 4
    event = json.loads(reporter.write_line.call_args.args[0].removeprefix("[TIA] "))
    assert event["deselection_notifications_without_nodeid"] == 4
    assert data["collection_seconds"] is None
    output.unlink()
    worker = testmon_logging.TestmonLogging(SimpleNamespace(workerinput={}, workeroutput={}))
    hook = worker.pytest_sessionfinish(None, 0)
    next(hook)
    assert "tia" in worker.config.workeroutput
    with pytest.raises(StopIteration):
        next(hook)
    assert not output.exists()
