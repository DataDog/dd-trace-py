import logging
import threading
import typing as t
from unittest import mock

import pytest

from ddtrace.internal import service
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


def test_flush_skips_malloc_trim_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    trim_calls: list[int] = []

    def fake_trim(pad: int) -> int:
        trim_calls.append(pad)
        return 1

    monkeypatch.setattr(profiling_config, "malloc_trim_enabled", False)
    monkeypatch.setattr(scheduler, "_malloc_trim_fn", fake_trim)
    with mock.patch.object(ddup, "upload"):
        scheduler.Scheduler().flush()

    assert trim_calls == []


def test_flush_calls_malloc_trim_after_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def fake_upload(*args: t.Any, **kwargs: t.Any) -> None:
        order.append("upload")

    def fake_trim(pad: int) -> int:
        order.append("trim")
        assert pad == 0
        return 1

    monkeypatch.setattr(profiling_config, "malloc_trim_enabled", True)
    monkeypatch.setattr(scheduler, "_malloc_trim_fn", fake_trim)
    monkeypatch.setattr(scheduler, "_malloc_trim_upload_count", 0)
    monkeypatch.setattr(scheduler, "_malloc_trim_last_rss_bytes", None)
    with mock.patch.object(ddup, "upload", fake_upload):
        scheduler.Scheduler().flush()

    assert order == ["upload", "trim"]


def test_flush_trims_after_upload_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    trim_calls: list[int] = []

    def fake_trim(pad: int) -> int:
        trim_calls.append(pad)
        return 1

    monkeypatch.setattr(profiling_config, "malloc_trim_enabled", True)
    monkeypatch.setattr(scheduler, "_malloc_trim_fn", fake_trim)
    monkeypatch.setattr(scheduler, "_malloc_trim_upload_count", 0)
    monkeypatch.setattr(scheduler, "_malloc_trim_last_rss_bytes", None)
    with mock.patch.object(ddup, "upload", side_effect=RuntimeError("upload failed")):
        scheduler.Scheduler().flush()

    assert trim_calls == [0]


def test_malloc_trim_pad_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    trim_calls: list[int] = []

    def fake_trim(pad: int) -> int:
        trim_calls.append(pad)
        return 1

    monkeypatch.setenv("_DD_PROFILING_MALLOC_TRIM_PAD", "65536")
    monkeypatch.setattr(profiling_config, "malloc_trim_enabled", True)
    monkeypatch.setattr(scheduler, "_malloc_trim_fn", fake_trim)
    monkeypatch.setattr(scheduler, "_malloc_trim_upload_count", 0)
    monkeypatch.setattr(scheduler, "_malloc_trim_last_rss_bytes", None)
    monkeypatch.setattr(scheduler, "_read_rss_bytes", lambda: None)
    with mock.patch.object(ddup, "upload"):
        scheduler.Scheduler().flush()

    assert trim_calls == [65536]


def test_malloc_trim_every_n_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    trim_calls: list[int] = []

    def fake_trim(pad: int) -> int:
        trim_calls.append(pad)
        return 1

    monkeypatch.setenv("_DD_PROFILING_MALLOC_TRIM_EVERY", "3")
    monkeypatch.setattr(profiling_config, "malloc_trim_enabled", True)
    monkeypatch.setattr(scheduler, "_malloc_trim_fn", fake_trim)
    monkeypatch.setattr(scheduler, "_malloc_trim_upload_count", 0)
    monkeypatch.setattr(scheduler, "_malloc_trim_last_rss_bytes", None)
    monkeypatch.setattr(scheduler, "_read_rss_bytes", lambda: None)
    with mock.patch.object(ddup, "upload"):
        for _ in range(5):
            scheduler.Scheduler().flush()

    # Uploads 1 and 2 skip; 3 and (after reset count continues) 6 would trim —
    # with 5 flushes, only upload 3 trims.
    assert trim_calls == [0]
    assert scheduler._malloc_trim_upload_count == 5


def test_malloc_trim_rss_growth_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    trim_calls: list[int] = []
    # Per-flush rss_before, plus rss_after after each trim:
    # flush1 before/after 100MB (baseline), flush2 before 120MB (skip),
    # flush3 before/after 180MB (trim).
    mb: int = 1024 * 1024
    rss_values: list[t.Optional[int]] = [
        100 * mb,
        100 * mb,
        120 * mb,
        180 * mb,
        180 * mb,
    ]
    rss_idx: list[int] = [0]

    def fake_trim(pad: int) -> int:
        trim_calls.append(pad)
        return 1

    def fake_rss() -> t.Optional[int]:
        idx: int = rss_idx[0]
        value: t.Optional[int] = rss_values[idx] if idx < len(rss_values) else rss_values[-1]
        rss_idx[0] = idx + 1
        return value

    monkeypatch.setenv("_DD_PROFILING_MALLOC_TRIM_RSS_GROWTH_MB", "64")
    monkeypatch.setattr(profiling_config, "malloc_trim_enabled", True)
    monkeypatch.setattr(scheduler, "_malloc_trim_fn", fake_trim)
    monkeypatch.setattr(scheduler, "_malloc_trim_upload_count", 0)
    monkeypatch.setattr(scheduler, "_malloc_trim_last_rss_bytes", None)
    monkeypatch.setattr(scheduler, "_read_rss_bytes", fake_rss)
    with mock.patch.object(ddup, "upload"):
        for _ in range(3):
            scheduler.Scheduler().flush()

    # First upload: no prior RSS baseline, so trim. Second: growth 20 MB < 64. Third: 80 MB.
    assert trim_calls == [0, 0]


def test_load_malloc_trim_missing_symbol_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoTrimLib:
        def __getattr__(self, name: str) -> t.Any:
            raise AttributeError(name)

    monkeypatch.setattr(scheduler, "_malloc_trim_fn", scheduler._UNSET)
    with mock.patch("ddtrace.profiling.scheduler.ctypes.CDLL", return_value=_NoTrimLib()):
        assert scheduler._get_malloc_trim() is None


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
