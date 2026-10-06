"""Bootstrapping code that is run when using `ddtrace.profiling.auto`."""

import functools
import json
import os
import platform
import sys
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal import service
from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import PeriodicThread
import ddtrace.profiling as profiling
from ddtrace.profiling import bootstrap


if TYPE_CHECKING:
    from ddtrace.profiling import collector
    from ddtrace.profiling.collector import memalloc

LOG = get_logger(__name__)

_CONFIG_PATH: str = os.path.join(os.getcwd(), "profiler_config.json")
_config_mtime_ns: Optional[int] = None
_config_watcher: Optional[PeriodicThread] = None


# MemoryCollector is not a Service and has no status, so we track the state of each collector we
# toggle. Collectors present in the profiler at start time are running.
_collector_running: dict[int, bool] = {}


def _set_collector_enabled(
    profiler_instance: Any, collector_class: type, factory: Callable[[], Any], enabled: bool
) -> None:
    impl: Any = profiler_instance._profiler
    with impl._service_lock:
        if impl.status != service.ServiceStatus.RUNNING:
            return
        col: Any = next((c for c in impl._collectors if type(c) is collector_class), None)
        if col is None:
            if not enabled:
                return
            col = factory()
            impl._collectors.append(col)
            _collector_running[id(col)] = False

        if _collector_running.get(id(col), True) == enabled:
            return
        try:
            if enabled:
                LOG.info("Starting collector %s", collector_class.__name__)
                col.start()
            else:
                LOG.info("Stopping collector %s", collector_class.__name__)
                col.stop()
        except Exception:
            LOG.error("Failed to %s collector %r", "start" if enabled else "stop", col, exc_info=True)
            return
        _collector_running[id(col)] = enabled
        LOG.info("Profiling collector %s %s", collector_class.__name__, "enabled" if enabled else "disabled")


def _is_collector_running(col: "collector.Collector | memalloc.MemoryCollector") -> bool:
    if isinstance(col, service.Service):
        return col.status == service.ServiceStatus.RUNNING
    return _collector_running.get(id(col), True)


def _sync_scheduler(profiler_instance: Any) -> None:
    """Pause uploads while no collector runs, and resume them when one runs again."""
    impl: Any = profiler_instance._profiler
    with impl._service_lock:
        scheduler: Any = impl._scheduler
        if scheduler is None or impl.status != service.ServiceStatus.RUNNING:
            return
        active: bool = any(_is_collector_running(c) for c in impl._collectors)
        if not active and scheduler.status == service.ServiceStatus.RUNNING:
            LOG.info("No profiling collector is running, pausing profile uploads")
            scheduler.stop()
            scheduler.join()
            # Upload what the collectors recorded before they stopped, so it does not go into the
            # first profile after the uploads resume.
            scheduler.flush()
        elif active and scheduler.status == service.ServiceStatus.STOPPED:
            LOG.info("A profiling collector is running again, resuming profile uploads")
            scheduler.interval = scheduler._configured_interval
            scheduler.start()


def _check_config_file() -> None:
    _apply_config_file()
    # Also run when the file did not change: an import hook can start a lock collector at any time.
    profiler_instance: Any = getattr(bootstrap, "profiler", None)
    if profiler_instance is not None:
        _sync_scheduler(profiler_instance)


def _apply_config_file() -> None:
    global _config_mtime_ns

    try:
        mtime_ns: int = os.stat(_CONFIG_PATH).st_mtime_ns
    except OSError:
        return
    if mtime_ns == _config_mtime_ns:
        return
    _config_mtime_ns = mtime_ns

    try:
        with open(_CONFIG_PATH) as f:
            data: Any = json.load(f)
    except (OSError, ValueError):
        LOG.warning("Could not read profiler config file %s", _CONFIG_PATH, exc_info=True)
        return
    if not isinstance(data, dict):
        LOG.warning("Profiler config file %s must contain a JSON object", _CONFIG_PATH)
        return

    profiler_instance: Any = getattr(bootstrap, "profiler", None)
    if profiler_instance is None:
        return

    from ddtrace.profiling.collector import asyncio
    from ddtrace.profiling.collector import exception
    from ddtrace.profiling.collector import memalloc
    from ddtrace.profiling.collector import stack
    from ddtrace.profiling.collector import threading

    if isinstance(data.get("stack"), bool):
        _set_collector_enabled(
            profiler_instance,
            stack.StackCollector,
            lambda: stack.StackCollector(tracer=profiler_instance.tracer),
            data["stack"],
        )
    if isinstance(data.get("memory"), bool):
        _set_collector_enabled(profiler_instance, memalloc.MemoryCollector, memalloc.MemoryCollector, data["memory"])
    if isinstance(data.get("exception"), bool):
        _set_collector_enabled(
            profiler_instance, exception.ExceptionCollector, exception.ExceptionCollector, data["exception"]
        )
    if isinstance(data.get("lock"), bool):
        lock_classes: list[type] = [
            threading.ThreadingLockCollector,
            threading.ThreadingRLockCollector,
            threading.ThreadingSemaphoreCollector,
            threading.ThreadingBoundedSemaphoreCollector,
            threading.ThreadingConditionCollector,
            asyncio.AsyncioLockCollector,
            asyncio.AsyncioSemaphoreCollector,
            asyncio.AsyncioBoundedSemaphoreCollector,
            asyncio.AsyncioConditionCollector,
        ]
        impl: Any = profiler_instance._profiler
        if not data["lock"]:
            # Otherwise the import hooks would start a lock collector again when its module is imported.
            impl._unregister_collectors_on_import()
        for lock_class in lock_classes:
            _set_collector_enabled(
                profiler_instance,
                lock_class,
                functools.partial(lock_class, tracer=profiler_instance.tracer),
                data["lock"],
            )
        if data["lock"]:
            impl._register_collectors_on_import()


def _start_config_watcher() -> None:
    global _config_watcher

    if _config_watcher is not None:
        return
    _config_watcher = PeriodicThread(
        1.0,
        target=_check_config_file,
        name="ddtrace.profiling.bootstrap:config_watcher",
        no_wait_at_start=True,
    )
    _config_watcher.start()


def start_profiler() -> None:
    if not profiling.is_available:
        LOG.warning(
            "The Datadog Profiler could not be started because native extensions are not "
            "available on this Python version: %s",
            profiling.failure_msg,
        )
        return

    from ddtrace.profiling import profiler

    if hasattr(bootstrap, "profiler"):
        bootstrap.profiler.stop()  # pyright: ignore[reportAttributeAccessIssue, reportCallIssue]

    # Export the profiler so we can introspect it if needed
    profiler_instance = profiler.Profiler()
    bootstrap.profiler = profiler_instance  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]
    bootstrap.profiler.start()  # type: ignore[attr-defined]  # pyright: ignore[reportCallIssue]
    _start_config_watcher()


if platform.system() == "Linux" and not (sys.maxsize > (1 << 32)):
    LOG.error(
        "The Datadog Profiler is not supported on 32-bit Linux systems. "
        "To use the profiler, please upgrade to a 64-bit Linux system. "
        "If you believe this is an error or need assistance, please report it at "
        "https://github.com/DataDog/dd-trace-py/issues"
    )
elif platform.system() == "Windows":
    LOG.error(
        "The Datadog Profiler is not supported on Windows. "
        "To use the profiler, please use a 64-bit Linux or macOS system. "
        "If you need assistance related to Windows support for the Profiler, please open a ticket at "
        "https://github.com/DataDog/dd-trace-py/issues"
    )
else:
    start_profiler()
