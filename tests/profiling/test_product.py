import threading
import types
import typing as t

import pytest

from ddtrace.internal.profiling import lifecycle
from ddtrace.internal.service import ServiceStatus
from ddtrace.profiling import bootstrap
from ddtrace.profiling import profiler as profiler_module


@pytest.fixture
def created_profilers(monkeypatch: pytest.MonkeyPatch) -> t.Iterator[list[dict[str, bool]]]:
    created: list[dict[str, bool]] = []

    class FakeProfiler:
        def __init__(self, **kwargs: bool) -> None:
            created.append(kwargs)
            self._profiler: types.SimpleNamespace = types.SimpleNamespace(status=ServiceStatus.STOPPED, **kwargs)

        def start(self) -> None:
            self._profiler.status = ServiceStatus.RUNNING

        def stop(self, flush: bool = True) -> None:
            self._profiler.status = ServiceStatus.STOPPED

    monkeypatch.setattr(profiler_module, "Profiler", FakeProfiler)
    monkeypatch.delattr(bootstrap, "profiler", raising=False)
    yield created
    if hasattr(bootstrap, "profiler"):
        del bootstrap.profiler


def test_stop_profiler_stops_and_removes_profiler(created_profilers: list[dict[str, bool]]) -> None:
    started = profiler_module.Profiler()
    started.start()
    lifecycle.set_profiler(started)
    assert lifecycle.current_profiler() is started

    with lifecycle.lock:
        lifecycle.stop_profiler()

    assert started._profiler.status == ServiceStatus.STOPPED
    assert lifecycle.current_profiler() is None
    assert not hasattr(bootstrap, "profiler")


def test_stop_profiler_without_profiler_is_a_no_op(created_profilers: list[dict[str, bool]]) -> None:
    with lifecycle.lock:
        lifecycle.stop_profiler()

    assert lifecycle.current_profiler() is None


def test_start_profiler_replaces_profiler(created_profilers: list[dict[str, bool]]) -> None:
    # Importing sitecustomize starts a profiler, so it is imported once Profiler is replaced.
    from ddtrace.profiling.bootstrap import sitecustomize

    sitecustomize.start_profiler()
    first = bootstrap.profiler
    sitecustomize.start_profiler()

    assert first._profiler.status == ServiceStatus.STOPPED
    assert bootstrap.profiler is not first
    assert bootstrap.profiler._profiler.status == ServiceStatus.RUNNING


def test_start_profiler_waits_for_lifecycle_lock(created_profilers: list[dict[str, bool]]) -> None:
    # Importing sitecustomize starts a profiler, so it is imported once Profiler is replaced.
    from ddtrace.profiling.bootstrap import sitecustomize

    # Stop the profiler before starting the new one.
    with lifecycle.lock:
        lifecycle.stop_profiler()
        created_profilers.clear()

    with lifecycle.lock:
        thread = threading.Thread(target=sitecustomize.start_profiler)
        thread.start()
        thread.join(timeout=0.1)
        assert thread.is_alive()
        assert created_profilers == []

    thread.join()
    assert created_profilers == [{}]
    assert bootstrap.profiler._profiler.status == ServiceStatus.RUNNING


def test_product_activated_only_with_running_profiler(created_profilers: list[dict[str, bool]]) -> None:
    from ddtrace.internal.profiling import product

    assert not product.activated()

    lifecycle.set_profiler(profiler_module.Profiler())
    assert product.activated()

    with lifecycle.lock:
        lifecycle.stop_profiler()
    assert not product.activated()


@pytest.mark.subprocess(env={"DD_PROFILING_ENABLED": "false"})
def test_loading_product_does_not_import_profiler() -> None:
    # The product manager loads every product, so this runs in every process, with profiling enabled or not.
    # Importing ddtrace.profiling would load the whole profiler and install its asyncio hooks.
    import sys

    from ddtrace.internal.products import get_product_entry_points

    (entry_point,) = [ep for ep in get_product_entry_points() if ep.name == "profiler"]
    entry_point.load()

    assert "ddtrace.profiling" not in sys.modules
