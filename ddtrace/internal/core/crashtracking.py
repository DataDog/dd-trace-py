from ddtrace import config
from ddtrace import version
from ddtrace.internal import agent
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.runtime import on_runtime_id_change
from ddtrace.settings.crashtracker import config as crashtracker_config


is_enabled_and_available = False
if crashtracker_config.enabled:
    try:
        from ddtrace.internal.datadog.profiling import crashtracker

        is_enabled_and_available = crashtracker.is_available
    except ImportError:
        is_enabled_and_available = False


def is_started():
    if crashtracker_config.enabled:
        try:
            from ddtrace.internal.datadog.profiling import crashtracker

            return crashtracker.is_started()
        except ImportError:
            return False


def is_available():
    return is_enabled_and_available


# DEV crashtracker was once loaded and enabled by default everywhere, but due to consequences of running long-running
#     child processes, it has been disabled by default. Hoping to re-enable it soon.  Pushing the module imports down
#     into individual functions is not a design requirement, but rather a way to avoid loading the module until needed.


@on_runtime_id_change
def _update_runtime_id(runtime_id: str) -> None:
    if is_enabled_and_available:
        from ddtrace.internal.datadog.profiling import crashtracker

        crashtracker.set_runtime_id(runtime_id)


def add_tag(key: str, value: str) -> None:
    if is_enabled_and_available:
        from ddtrace.internal.datadog.profiling import crashtracker

        crashtracker.set_tag(key, value)


def start() -> bool:
    if not is_enabled_and_available:
        return False

    import platform

    from ddtrace.internal.datadog.profiling import crashtracker

    crashtracker.set_url(crashtracker_config.debug_url or agent.get_trace_url())
    crashtracker.set_service(config.service)
    crashtracker.set_version(config.version)
    crashtracker.set_env(config.env)
    crashtracker.set_runtime_id(get_runtime_id())
    crashtracker.set_runtime_version(platform.python_version())
    crashtracker.set_library_version(version.get_version())
    crashtracker.set_alt_stack(bool(crashtracker_config.alt_stack))
    crashtracker.set_wait_for_receiver(bool(crashtracker_config.wait_for_receiver))
    if crashtracker_config.stacktrace_resolver == "fast":
        crashtracker.set_resolve_frames_fast()
    elif crashtracker_config.stacktrace_resolver == "full":
        crashtracker.set_resolve_frames_full()
    elif crashtracker_config.stacktrace_resolver == "safe":
        crashtracker.set_resolve_frames_safe()
    else:
        crashtracker.set_resolve_frames_disable()

    if crashtracker_config.stdout_filename:
        crashtracker.set_stdout_filename(crashtracker_config.stdout_filename)
    if crashtracker_config.stderr_filename:
        crashtracker.set_stderr_filename(crashtracker_config.stderr_filename)

    # Add user tags
    for key, value in crashtracker_config.tags.items():
        add_tag(key, value)

    return crashtracker.start()
