import platform
import shutil
from typing import Dict
from typing import Optional

from ddtrace import config
from ddtrace import version
from ddtrace.internal import forksafe
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.settings._agent import config as agent_config
from ddtrace.settings.crashtracker import config as crashtracker_config
from ddtrace.settings.profiling import config as profiling_config
from ddtrace.settings.profiling import config_str


is_available = True
try:
    from ddtrace.internal.native._native import CrashtrackerConfiguration
    from ddtrace.internal.native._native import CrashtrackerMetadata
    from ddtrace.internal.native._native import CrashtrackerReceiverConfig
    from ddtrace.internal.native._native import CrashtrackerStatus
    from ddtrace.internal.native._native import StacktraceCollection
    from ddtrace.internal.native._native import crashtracker_init
    from ddtrace.internal.native._native import crashtracker_on_fork
    from ddtrace.internal.native._native import crashtracker_status
except ImportError:
    is_available = False


def _get_tags(additional_tags: Optional[Dict[str, str]]) -> Dict[str, str]:
    tags = {
        "language": "python",
        "runtime": "CPython",
        "is_crash": "true",
        "severity": "crash",
    }

    # Here we check for the presence of each tags, as the underlying Metadata
    # object expects std::collections::HashMap<String, String> which doesn't
    # support None values.
    if config.env:
        tags["env"] = config.env
    if config.service:
        tags["service"] = config.service
    if config.version:
        tags["version"] = config.version
    runtime_id = get_runtime_id()
    if runtime_id:
        tags["runtime_id"] = runtime_id
    runtime_version = platform.python_version()
    if runtime_version:
        tags["runtime_version"] = runtime_version
    library_version = version.get_version()
    if library_version:
        tags["library_version"] = library_version

    for k, v in crashtracker_config.tags.items():
        if k and v:
            tags[k] = v

    if additional_tags:
        for k, v in additional_tags.items():
            if k and v:
                tags[k] = v

def start() -> bool:
    if not is_available:
        print(failure_msg)
        return False

    import platform

    crashtracker.set_url(crashtracker_config.debug_url or agent_config.trace_agent_url)
    crashtracker.set_service(config.service)
    crashtracker.set_version(config.version)
    crashtracker.set_env(config.env)
    crashtracker.set_runtime_id(get_runtime_id())
    crashtracker.set_runtime_version(platform.python_version())
    crashtracker.set_library_version(version.get_version())
    crashtracker.set_create_alt_stack(bool(crashtracker_config.create_alt_stack))
    crashtracker.set_use_alt_stack(bool(crashtracker_config.use_alt_stack))
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

    if profiling_config.enabled:
        tags["profiler_config"] = config_str(profiling_config)

    # Another thing to note is that these tag keys and values can be bytes
    # objects, so we need to ensure that they are converted to strings as
    # PyO3 can't convert bytes objects to Rust String type.
    tags = {ensure_text(k): ensure_text(v) for k, v in tags.items()}

    return tags


def _get_args(additional_tags: Optional[Dict[str, str]]):
    dd_crashtracker_receiver = shutil.which("_dd_crashtracker_receiver")
    if dd_crashtracker_receiver is None:
        print("Failed to find _dd_crashtracker_receiver")
        return (None, None, None)

    if crashtracker_config.stacktrace_resolver is None:
        stacktrace_resolver = StacktraceCollection.Disabled
    elif crashtracker_config.stacktrace_resolver == "fast":
        stacktrace_resolver = StacktraceCollection.WithoutSymbols
    elif crashtracker_config.stacktrace_resolver == "safe":
        stacktrace_resolver = StacktraceCollection.EnabledWithSymbolsInReceiver
    elif crashtracker_config.stacktrace_resolver == "full":
        stacktrace_resolver = StacktraceCollection.EnabledWithInprocessSymbols
    else:
        # This should never happen, as the value is validated in the crashtracker_config
        # module.
        print(f"Invalid stacktrace_resolver value: {crashtracker_config.stacktrace_resolver}")
        stacktrace_resolver = StacktraceCollection.EnabledWithInprocessSymbols

    # Create crashtracker configuration
    config = CrashtrackerConfiguration(
        [],  # additional_files
        crashtracker_config.create_alt_stack,
        crashtracker_config.use_alt_stack,
        5000,  # timeout_ms
        stacktrace_resolver,
        crashtracker_config.debug_url or agent_config.trace_agent_url,
        None,  # unix_socket_path
    )

    # Create crashtracker receiver configuration
    receiver_config = CrashtrackerReceiverConfig(
        [],  # args
        {},  # env
        dd_crashtracker_receiver,  # path_to_receiver_binary
        crashtracker_config.stderr_filename,
        crashtracker_config.stdout_filename,
    )

    tags = _get_tags(additional_tags)

    metadata = CrashtrackerMetadata("dd-trace-py", version.get_version(), "python", tags)

    return config, receiver_config, metadata


def is_started() -> bool:
    if not is_available:
        return False
    return crashtracker_status() == CrashtrackerStatus.Initialized


def start(additional_tags: Optional[Dict[str, str]] = None) -> bool:
    if not is_available:
        return False
    if not crashtracker_config.enabled:
        return False

    try:
        config, receiver_config, metadata = _get_args(additional_tags)
        if config is None or receiver_config is None or metadata is None:
            print("Failed to start crashtracker: failed to construct crashtracker configuration")
            return False

        crashtracker_init(config, receiver_config, metadata)

        def crashtracker_fork_handler():
            # We recreate the args here mainly to pass updated runtime_id after
            # fork
            config, receiver_config, metadata = _get_args(additional_tags)
            if config is None or receiver_config is None or metadata is None:
                print("Failed to restart crashtracker after fork: failed to construct crashtracker configuration")
                return
            crashtracker_on_fork(config, receiver_config, metadata)

        forksafe.register(crashtracker_fork_handler)
    except Exception as e:
        print(f"Failed to start crashtracker: {e}")
        return False
    return True
