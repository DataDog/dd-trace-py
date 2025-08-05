import enum

from ddtrace.settings.dynamic_instrumentation import config


requires = ["remote-configuration"]


def post_preload():
    pass


def _start():
    from ddtrace.debugging import DynamicInstrumentation

    DynamicInstrumentation.enable()


def start():
    from ddtrace.debugging._import import DebuggerModuleWatchdog

    # We need to install this on start-up because if DI gets enabled remotely
    # we won't be able to capture many of the code objects from the modules
    # that are already loaded.
    DebuggerModuleWatchdog.install()

    if config.enabled:
        _start()


def before_fork():
    # We need to make sure that each process shares the same RC data connector
    import ddtrace.debugging._probe.remoteconfig  # noqa


def restart(join=False):
    # Nothing to do
    pass


def stop(join=False):
    if config.enabled:
        from ddtrace.debugging import DynamicInstrumentation

        DynamicInstrumentation.disable(join=join)


def at_exit(join=False):
    stop(join=join)


class APMCapabilities(enum.IntFlag):
    APM_TRACING_ENABLE_DYNAMIC_INSTRUMENTATION = 1 << 38


def apm_tracing_rc(lib_config, _config):
    if (enabled := lib_config.get("dynamic_instrumentation_enabled")) is not None:
        should_start = (config.spec.enabled.full_name not in config.source or config.enabled) and enabled
        _start() if should_start else stop()
