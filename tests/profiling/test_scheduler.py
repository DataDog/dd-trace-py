import json
import logging
from pathlib import Path
import threading
import time
import typing as t
from unittest import mock

import pytest

from ddtrace.internal import service
from ddtrace.internal import threads
from ddtrace.internal.datadog.profiling import code_provenance
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.settings.profiling import config as profiling_config
from ddtrace.profiling import scheduler


def test_interval_is_read_when_built(monkeypatch: pytest.MonkeyPatch) -> None:
    # Remote config changes the upload interval after the scheduler module is imported.
    monkeypatch.setattr(profiling_config, "upload_interval", 12.0)

    assert scheduler.Scheduler().interval == 12.0


def test_serverless_scheduler_interval_none_uses_forced_interval() -> None:
    s = scheduler.ServerlessScheduler(interval=None)

    assert s.interval == scheduler.ServerlessScheduler.FORCED_INTERVAL


def test_exporter_failure(caplog: pytest.LogCaptureFixture) -> None:
    s = scheduler.Scheduler()

    with caplog.at_level(logging.DEBUG, logger="ddtrace.profiling.scheduler"):
        with mock.patch.object(ddup, "upload", side_effect=RuntimeError("LOL")):
            s.flush()

    assert (
        "ddtrace.profiling.scheduler",
        logging.ERROR,
        "[profiling] Failed to upload profile",
    ) in caplog.record_tuples


def test_periodic_survives_export_failure() -> None:
    attempts: list[None] = []
    exported_again = threading.Event()

    def flaky_upload(*args: t.Any, **kwargs: t.Any) -> None:
        attempts.append(None)
        if len(attempts) == 1:
            raise RuntimeError("transient export failure")
        if len(attempts) >= 3:
            exported_again.set()

    s = scheduler.Scheduler(interval=0.05)
    with mock.patch.object(ddup, "upload", flaky_upload):
        s.start()
        try:
            assert exported_again.wait(30), "scheduler stopped exporting after %d attempt(s)" % len(attempts)
        finally:
            s.stop()
            s.join()


def test_start_builds_code_provenance_in_background() -> None:
    build_started: threading.Event = threading.Event()
    release_build: threading.Event = threading.Event()
    build_finished: threading.Event = threading.Event()
    build_thread_ids: list[int] = []

    def slow_build() -> None:
        build_thread_ids.append(threading.get_ident())
        build_started.set()
        try:
            release_build.wait(5)
        finally:
            build_finished.set()

    s: scheduler.Scheduler = scheduler.Scheduler()
    s._enable_code_provenance = True
    with mock.patch.object(code_provenance, "get_code_provenance_file", slow_build):
        s.start()
        try:
            assert build_started.wait(30), "code provenance was not built on start"
            assert build_thread_ids != [threading.get_ident()], "start waited for the code provenance build"
            assert s.status == service.ServiceStatus.RUNNING
        finally:
            release_build.set()
            s.stop()
            s.join()
            assert build_finished.wait(30)

    assert len(build_thread_ids) == 1


def test_start_skips_code_provenance_build_when_disabled() -> None:
    s: scheduler.Scheduler = scheduler.Scheduler()
    s._enable_code_provenance = False
    with mock.patch.object(code_provenance, "get_code_provenance_file") as build:
        s.start()
        s.stop()
        s.join()

    build.assert_not_called()


def test_start_survives_code_provenance_build_failure() -> None:
    build_done: threading.Event = threading.Event()

    def failing_build() -> None:
        build_done.set()
        raise RuntimeError("LOL")

    s: scheduler.Scheduler = scheduler.Scheduler()
    s._enable_code_provenance = True
    with mock.patch.object(code_provenance, "get_code_provenance_file", failing_build):
        s.start()
        try:
            assert build_done.wait(30)
            assert s.status == service.ServiceStatus.RUNNING
        finally:
            s.stop()
            s.join()


def _start_with_held_build(
    s: scheduler.Scheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[threading.Event, list[int]]:
    """Start ``s`` on a cold cache with the background build held until the returned event is set."""
    cache_file: Path = tmp_path / "code-provenance.json"
    lock_file: Path = tmp_path / "code-provenance.lock"
    monkeypatch.setattr(code_provenance, "_cache_file_and_lock", lambda: (cache_file, lock_file))
    monkeypatch.setattr(code_provenance, "_code_provenance_file_path", None)

    build_started: threading.Event = threading.Event()
    release_build: threading.Event = threading.Event()
    computes: list[int] = []

    def compute() -> str:
        computes.append(threading.get_ident())
        if len(computes) == 1:
            build_started.set()
            release_build.wait(10)
        return json.dumps({"v1": []})

    monkeypatch.setattr(code_provenance, "_compute_json_str", compute)
    s._enable_code_provenance = True
    s.start()
    assert build_started.wait(10), "code provenance was not built on start"
    return release_build, computes


def test_flush_during_background_build_builds_the_file_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A process that exits before the background build finishes: stop does not wait for it,
    # and the shutdown flush builds the file itself.
    uploaded: list[t.Optional[str]] = []

    def upload(*args: t.Any, **kwargs: t.Any) -> None:
        uploaded.append(code_provenance.get_code_provenance_file())

    s: scheduler.Scheduler = scheduler.Scheduler()
    with mock.patch.object(ddup, "upload", upload):
        release_build, computes = _start_with_held_build(s, tmp_path, monkeypatch)
        try:
            s.stop()
            s.join()
            s.flush()
        finally:
            release_build.set()

    cache_file: Path = tmp_path / "code-provenance.json"
    assert uploaded == [str(cache_file)]
    assert len(computes) == 2, "the flush did not build the file itself"
    assert computes[0] != computes[1]
    deadline: float = time.monotonic() + 10
    while s._code_provenance_thread is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert s._code_provenance_thread is None
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"v1": []}


def test_fork_waits_for_background_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Same as any one-shot ddtrace thread: the before-fork hook joins it, so a fork during a
    # cold build waits for the build to finish.
    s: scheduler.Scheduler = scheduler.Scheduler()
    with mock.patch.object(ddup, "upload"):
        release_build, computes = _start_with_held_build(s, tmp_path, monkeypatch)
        s.stop()
        s.join()

    hold_s: float = 0.2

    def release_later() -> None:
        time.sleep(hold_s)
        release_build.set()

    releaser: threading.Thread = threading.Thread(target=release_later)
    releaser.start()
    started_at: float = time.monotonic()
    try:
        threads._before_fork()
        waited_s: float = time.monotonic() - started_at
        assert release_build.is_set(), "fork did not wait for the build"
        assert waited_s >= hold_s * 0.9
        assert s._code_provenance_thread is None
        assert len(computes) == 1
        threads._after_fork_parent()
        time.sleep(0.3)
    finally:
        release_build.set()
        releaser.join(10)
        threads.ThreadRestartTimer.clear()
        threads._threads_to_restart_after_fork.clear()
        threads._forking = False


def test_thread_name() -> None:
    s = scheduler.Scheduler()
    s.start()
    assert s._worker is not None
    assert s._worker.name == "ddtrace.profiling.scheduler:Scheduler"
    s.stop()


def test_before_flush() -> None:
    x: dict[str, bool] = {}

    def call_me() -> None:
        x["OK"] = True

    s = scheduler.Scheduler(before_flush=call_me)
    s.flush()
    assert x["OK"]


def test_before_flush_failure(caplog: pytest.LogCaptureFixture) -> None:
    def call_me() -> None:
        raise Exception("LOL")

    s = scheduler.Scheduler(before_flush=call_me)
    s.flush()
    assert caplog.record_tuples == [
        (("ddtrace.profiling.scheduler", logging.ERROR, "[profiling] Scheduler before_flush hook failed"))
    ]


@mock.patch("ddtrace.profiling.scheduler.Scheduler.periodic")
@mock.patch("ddtrace.profiling.scheduler.time.time_ns")
def test_serverless_periodic(mock_time_ns: mock.MagicMock, mock_periodic: mock.MagicMock) -> None:
    s = scheduler.ServerlessScheduler()
    # Fake start()
    s._last_export = 0
    mock_time_ns.return_value = int(s.FORCED_INTERVAL * s.FLUSH_AFTER_INTERVALS * 1e9)

    for _ in range(int(s.FLUSH_AFTER_INTERVALS) - 1):
        s.periodic()

    assert s._profiled_intervals == s.FLUSH_AFTER_INTERVALS - 1
    mock_periodic.assert_not_called()

    s.periodic()

    assert s._profiled_intervals == 0
    assert s.interval == 1
    mock_periodic.assert_called_once_with()


def test_rollback_start_stops_started_worker() -> None:
    s = scheduler.Scheduler()
    s.start()
    worker = s._worker
    assert worker is not None

    s._rollback_start()
    s.join()

    assert s.status == service.ServiceStatus.STOPPED
    assert s._worker is worker


def test_rollback_start_without_worker() -> None:
    s = scheduler.Scheduler()

    s._rollback_start()
    s.join()

    assert s.status == service.ServiceStatus.STOPPED
