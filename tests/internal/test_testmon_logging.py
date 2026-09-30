from contextlib import closing
import gzip
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
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


@pytest.mark.parametrize("journal_mode", ["WAL", "DELETE"])
def test_database_gzip_roundtrip_preserves_committed_data(runner_module, tmp_path, journal_mode):
    database = tmp_path / ".testmondata"
    # Leave the WAL on disk as a worker exiting without closing SQLite would.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sqlite3, sys; "
            "db = sqlite3.connect(sys.argv[1]); "
            "db.execute('PRAGMA journal_mode=' + sys.argv[2]); "
            "db.execute('CREATE TABLE state (value TEXT)'); "
            "db.execute(\"INSERT INTO state VALUES ('committed')\"); "
            "db.commit(); os._exit(0)",
            str(database),
            journal_mode,
        ],
        check=True,
    )
    if journal_mode == "WAL":
        assert Path(str(database) + "-wal").stat().st_size > 0
    stored = runner_module._compress_testmon_database(database)
    archive = database.with_suffix(".gz")
    assert list(tmp_path.iterdir()) == [archive]
    assert stored["stored"]
    assert stored["compressed_bytes"] == archive.stat().st_size
    assert stored["database_bytes"] == len(gzip.decompress(archive.read_bytes()))
    assert stored["seconds"] >= stored["checkpoint_seconds"] + stored["gzip_seconds"] >= 0
    restored = runner_module._restore_testmon_database(database)
    assert restored["restored"]
    assert restored["compressed_bytes"] == stored["compressed_bytes"]
    assert restored["seconds"] >= 0
    assert list(tmp_path.iterdir()) == [database]
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT value FROM state").fetchall() == [("committed",)]
        connection.execute("INSERT INTO state VALUES ('next run')")
        connection.commit()
    runner_module._compress_testmon_database(database)
    runner_module._restore_testmon_database(database)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM state").fetchone() == (2,)


def test_busy_checkpoint_keeps_database_and_wal(runner_module, tmp_path):
    database = tmp_path / ".testmondata"
    with closing(sqlite3.connect(database)) as writer, closing(sqlite3.connect(database)) as reader:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE state (value TEXT)")
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM state").fetchall()
        writer.execute("INSERT INTO state VALUES ('committed')")
        writer.commit()
        with pytest.raises(sqlite3.OperationalError, match="checkpoint is busy"):
            runner_module._compress_testmon_database(database)
        assert database.exists()
        assert Path(str(database) + "-wal").stat().st_size > 0
        assert not database.with_suffix(".gz").exists()
        assert writer.execute("SELECT value FROM state").fetchone() == ("committed",)


def test_invalid_gzip_does_not_publish_partial_database(runner_module, tmp_path):
    database = tmp_path / ".testmondata"
    archive = database.with_suffix(".gz")
    archive.write_bytes(gzip.compress(b"partial database")[:-5])
    with pytest.raises(OSError, match="invalid testmon archive"):
        runner_module._restore_testmon_database(database)
    assert list(tmp_path.iterdir()) == [archive]


def test_gzip_failure_keeps_database(runner_module, monkeypatch, tmp_path):
    database = tmp_path / ".testmondata"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE state (value TEXT)")
    monkeypatch.setattr(runner_module.shutil, "copyfileobj", mock.Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        runner_module._compress_testmon_database(database)
    assert list(tmp_path.iterdir()) == [database]


def test_restore_refuses_stale_wal(runner_module, tmp_path):
    database = tmp_path / ".testmondata"
    archive = database.with_suffix(".gz")
    archive.write_bytes(gzip.compress(b"database"))
    wal = Path(str(database) + "-wal")
    wal.write_bytes(b"stale")
    with pytest.raises(ValueError, match="alongside uncompressed"):
        runner_module._restore_testmon_database(database)
    assert wal.read_bytes() == b"stale"
    assert archive.exists()


def test_database_compression_cold_start(runner_module, tmp_path):
    database = tmp_path / "missing" / ".testmondata"
    assert not runner_module._restore_testmon_database(database)["restored"]
    assert not runner_module._compress_testmon_database(database)["stored"]
    assert not database.parent.exists()


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
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL").fetchone()
        connection.execute("CREATE TABLE state (value TEXT)")
        connection.execute("INSERT INTO state VALUES ('incoming')")
        connection.commit()
    runner_module._compress_testmon_database(database)
    monkeypatch.setenv("DD_LLMOBS_TIA_DIAGNOSTICS", diagnostics)
    phases = []
    events = []
    monkeypatch.setattr(runner_module, "_tia_log", lambda event, **fields: events.append({"event": event, **fields}))

    def execute(command, **kwargs):
        env = dict(assignment.split("=", 1) for assignment in command[1:-3])
        phase = env["DD_LLMOBS_TIA_PHASE"]
        phases.append(phase)
        with closing(sqlite3.connect(env["TESTMON_DATAFILE"])) as connection:
            assert connection.execute("SELECT value FROM state").fetchone() == ("incoming",)
            connection.execute("UPDATE state SET value = ?", (phase,))
            connection.commit()
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
    assert list(database.parent.iterdir()) == [database.with_suffix(".gz")]
    assert next(event for event in events if event["event"] == "database_restore")["restored"]
    assert next(event for event in events if event["event"] == "database_store")["stored"]
    runner_module._restore_testmon_database(database)
    with closing(sqlite3.connect(database)) as connection:
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
