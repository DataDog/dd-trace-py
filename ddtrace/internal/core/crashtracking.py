import os
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

    if profiling_config.enabled:
        tags["profiler_config"] = config_str(profiling_config)

    # Another thing to note is that these tag keys and values can be bytes
    # objects, so we need to ensure that they are converted to strings as
    # PyO3 can't convert bytes objects to Rust String type.
    tags = {ensure_text(k): ensure_text(v) for k, v in tags.items()}

    return tags


def _get_args(additional_tags: Optional[Dict[str, str]]):
    # First check whether crashtracker_exe command is available
    crashtracker_exe = shutil.which("dd_crashtracker_exe")
    if not crashtracker_exe or not os.access(crashtracker_exe, os.X_OK):
        print("Failed to find crashtracker_exe in the scripts directory")
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
        crashtracker_exe,  # path_to_receiver_binary
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
