"""Product plugin that installs and starts the profiler."""

from ddtrace.internal.logger import get_logger
from ddtrace.internal.profiling import lifecycle
from ddtrace.internal.settings.profiling import config as profiling_config


log = get_logger(__name__)

requires: list[str] = []


def post_preload() -> None:
    pass


def enabled() -> bool:
    return bool(profiling_config.install) or bool(profiling_config.enabled)


def start() -> None:
    if profiling_config.install:
        # Install the stack profiler hooks now, so that the profiler can be enabled at runtime and still
        # see the threads, event loops and greenlets created before that. If the profiler is also enabled
        # below, it calls init_stack and initialize_gevent_support again: both install their hooks only once.
        try:
            from ddtrace.profiling.collector import _task as _profiling_task
            from ddtrace.profiling.collector import threading as _profiling_threading

            _profiling_threading.init_stack()
            _profiling_task.initialize_gevent_support()
        except Exception:
            log.debug("failed to install the profiling hooks", exc_info=True)

    if profiling_config.enabled:
        log.debug("profiler enabled via environment variable")
        try:
            import ddtrace.profiling.auto  # noqa: F401
        except Exception:
            log.error("failed to enable profiling", exc_info=True)


def activated() -> bool:
    # start can return without a running profiler: in armed mode, or when the platform or the native extensions
    # are not supported.
    return lifecycle.current_profiler() is not None


def restart(join: bool = False) -> None:
    # Nothing to do: the lifecycle lock is fork-safe.
    pass


def stop(join: bool = False) -> None:
    # The profiler registers its own atexit hook.
    pass
