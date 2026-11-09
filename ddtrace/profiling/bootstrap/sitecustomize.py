"""Bootstrapping code that is run when using `ddtrace.profiling.auto`."""

from ddtrace.internal.logger import get_logger
from ddtrace.internal.profiling import lifecycle
from ddtrace.internal.settings.profiling import _is_platform_supported
from ddtrace.internal.settings.profiling import config as profiling_config
import ddtrace.profiling as profiling


LOG = get_logger(__name__)


def start_profiler() -> None:
    if not profiling.is_available:
        LOG.warning(
            "The Datadog Profiler could not be started because native extensions are not "
            "available on this Python version: %s",
            profiling.failure_msg,
        )
        return

    from ddtrace.profiling import profiler

    with lifecycle.lock:
        lifecycle.stop_profiler()

        # The Profiler can be enabled but all Collectors disabled.
        # A profiler without collectors would still upload empty profiles.
        if not any(
            (
                profiling_config.stack.enabled,
                profiling_config.memory.enabled,
                profiling_config.lock.enabled,
                profiling_config.exception.enabled,
                profiling_config.pytorch.enabled,
                # Native heap does not use a collector, but is also started by the
                # Profiler object, so for the time being we include it here.
                profiling_config.native_heap.enabled,
            )
        ):
            LOG.debug("[profiling] No collector is enabled, not starting the profiler")
            return

        # Export the profiler so we can introspect it if needed
        new_profiler = profiler.Profiler()
        lifecycle.set_profiler(new_profiler)
        new_profiler.start()


_platform_unsupported_msg, _platform_is_supported = _is_platform_supported()

if not _platform_is_supported:
    LOG.error(
        "The Datadog Profiler is not supported on this platform: %s. "
        "To use the profiler, please use a supported Python version on a 64-bit Linux or macOS system. "
        "If you believe this is an error or need assistance, please report it at "
        "https://github.com/DataDog/dd-trace-py/issues",
        _platform_unsupported_msg,
    )
else:
    start_profiler()
