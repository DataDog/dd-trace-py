"""Tests for the multi-environment loop in scripts/run-tests."""

from importlib.machinery import SourceFileLoader
import importlib.util
import io
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
from unittest import mock

import pytest


_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "run-tests"


@pytest.fixture(scope="module")
def run_tests_mod():
    # The script reads every suitespec file at import time. The loop under test does not use the suite tables.
    suitespec = types.ModuleType("tests.suitespec")
    vars(suitespec).update(
        TestEnvironment=object,
        TestRun=object,
        get_patterns=lambda suite: set(),
        get_suites=lambda: {},
        get_test_environments=lambda nightly=False: {},
    )

    loader = SourceFileLoader("run_tests_script", str(_SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    original_path = list(sys.path)
    # The script sets COMPOSE_PROJECT_NAME at import time. Restore os.environ so that other tests do not see it.
    with mock.patch.dict(sys.modules, {"tests.suitespec": suitespec, spec.name: module}), mock.patch.dict(os.environ):
        loader.exec_module(module)
        try:
            yield module
        finally:
            sys.path[:] = original_path


def _environment(name: str):
    return types.SimpleNamespace(hash=name, python="3.12", runs=(types.SimpleNamespace(environment={}),))


@pytest.fixture
def runner(run_tests_mod, monkeypatch):
    monkeypatch.setattr(run_tests_mod.os, "environ", {"GITLAB_CI": "true"})
    instance = run_tests_mod.TestRunner()
    instance._prepare_environment = mock.Mock(side_effect=lambda environment, **kwargs: environment.hash)
    instance._command_environment = mock.Mock(return_value={})
    instance._build_environment = mock.Mock(return_value=False)
    instance._test_command = mock.Mock(side_effect=lambda environment, *args: ["run", environment.hash])
    return instance


def _run(runner, run_tests_mod, monkeypatch, environments, exit_codes):
    executed = []

    def fake_run(command, **kwargs):
        executed.append(command[1])
        return subprocess.CompletedProcess(command, exit_codes[command[1]])

    monkeypatch.setattr(run_tests_mod, "_run_in_process_group", fake_run)
    return runner._run_environments(environments, {}, ["--", "--ddtrace"], rebuild=False), executed


def test_each_environment_announces_itself_before_it_is_prepared(runner, run_tests_mod, monkeypatch, capsys):
    ok, executed = _run(
        runner,
        run_tests_mod,
        monkeypatch,
        [_environment("aaa"), _environment("bbb")],
        {"aaa": 0, "bbb": 0},
    )

    out = capsys.readouterr().out
    assert ok
    assert executed == ["aaa", "bbb"]
    assert out.index("Running test environment: aaa") < out.index("Preparing test environment aaa")
    assert out.index("Running test environment: bbb") < out.index("Preparing test environment bbb")


def test_first_failing_environment_stops_the_run(runner, run_tests_mod, monkeypatch, capsys):
    ok, executed = _run(
        runner,
        run_tests_mod,
        monkeypatch,
        [_environment("aaa"), _environment("bbb"), _environment("ccc")],
        {"aaa": 0, "bbb": 3, "ccc": 0},
    )

    out = capsys.readouterr().out
    assert not ok
    assert executed == ["aaa", "bbb"]
    assert "Running test environment: ccc" not in out


class _FlushRecorder(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushed = ""

    def flush(self):
        super().flush()
        self.flushed = self.getvalue()


def test_environment_announcement_is_flushed_before_the_environment_runs(runner, run_tests_mod, monkeypatch):
    # The test commands write straight to the job log, so an unflushed announcement lands after their output.
    stdout = _FlushRecorder()
    monkeypatch.setattr(sys, "stdout", stdout)
    flushed_at_run = []

    def fake_run(command, **kwargs):
        flushed_at_run.append(stdout.flushed)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_tests_mod, "_run_in_process_group", fake_run)
    assert runner._run_environments([_environment("aaa")], {}, ["--", "--ddtrace"], rebuild=False)

    assert "Running test environment: aaa" in flushed_at_run[0]


def test_stop_signal_reaches_the_child_process_group(run_tests_mod: types.ModuleType) -> None:
    with tempfile.TemporaryDirectory() as directory:
        ready = pathlib.Path(directory) / "ready"
        child = "\n".join(
            [
                "import signal, sys, time",
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(42))",
                f"open({str(ready)!r}, 'w').close()",
                "time.sleep(5)",
            ]
        )

        def _notify() -> None:
            deadline = time.monotonic() + 5
            while not ready.exists():
                if time.monotonic() >= deadline:
                    return
                time.sleep(0.01)
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=_notify, daemon=True).start()
        result = run_tests_mod._run_in_process_group(
            [sys.executable, "-c", child],
            env=os.environ.copy(),
            cwd=pathlib.Path.cwd(),
        )

    assert result.returncode == 42
