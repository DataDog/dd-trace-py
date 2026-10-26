"""Product plugin that installs and starts the profiler, and lets Remote Config control it."""

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import RemoteConfigProduct
from ddtrace.internal.profiling import remoteconfig
from ddtrace.internal.settings._config import config as global_config
from ddtrace.internal.settings.profiling import config as profiling_config


log = get_logger(__name__)

# Although we use Remote Configuration for Profiling, we do not require it as a dependency.
# The Profiler can start without it (if it's enabled through local configuration), so we
# should not prevent the Profiling Product from starting if it is not available. The start
# order does not matter either: the poller batches product enablements into one request.
requires: list[str] = []


def _remote_config_enabled() -> bool:
    return bool(global_config._remote_config_enabled) and bool(profiling_config.remote_config_experiment)


def post_preload() -> None:
    # The product manager defers start to the workers in a uWSGI master, but not post_preload. The hooks must be
    # installed before the master loads the application, and Profiler.start must run in the master to register its
    # own uWSGI postfork and atexit callbacks.
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


def enabled() -> bool:
    return bool(profiling_config.install) or bool(profiling_config.enabled) or _remote_config_enabled()


def start() -> None:
    if _remote_config_enabled():
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.register_callback(RemoteConfigProduct.Debug, remoteconfig.ProfilingRCCallback())
        remoteconfig_poller.enable_product(RemoteConfigProduct.Debug)


def activated() -> bool:
    # The product manager calls this after start, before post_preload starts the profiler. The configuration is
    # already disabled when the platform or the native extensions are not supported, and in armed mode.
    return bool(profiling_config.enabled)


def restart(join: bool = False) -> None:
    # Nothing to do: the lifecycle lock is fork-safe.
    pass


def stop(join: bool = False) -> None:
    # The profiler registers its own atexit hook, so only the Remote Config subscription is undone here.
    if not _remote_config_enabled():
        return

    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_poller.unregister_callback(RemoteConfigProduct.Debug)
    remoteconfig_poller.disable_product(RemoteConfigProduct.Debug)
