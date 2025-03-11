import os
import platform
import shutil
import sys
from typing import Dict
from typing import Optional
from typing import Tuple

from ddtrace import config
from ddtrace import version
from ddtrace.internal import agent
from ddtrace.internal import forksafe
from ddtrace.internal.native._native import CrashtrackerConfiguration
from ddtrace.internal.native._native import CrashtrackerReceiverConfig
from ddtrace.internal.native._native import CrashtrackerStatus
from ddtrace.internal.native._native import Metadata
from ddtrace.internal.native._native import StacktraceCollection
from ddtrace.internal.native._native import crashtracker_init
from ddtrace.internal.native._native import crashtracker_on_fork
from ddtrace.internal.native._native import crashtracker_status
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.settings.crashtracker import config as crashtracker_config
from ddtrace.settings.profiling import config as profiling_config
from ddtrace.settings.profiling import config_str


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

    return tags


def _get_args(
    additional_tags: Optional[Dict[str, str]]
) -> Tuple[Optional[CrashtrackerConfiguration], Optional[CrashtrackerReceiverConfig], Optional[Metadata]]:
    # First check whether crashtracker_exe command is available
    crashtracker_exe = shutil.which("crashtracker_exe")
    if crashtracker_exe is None:
        # Failed to find crashtracker_exe from PATH, check if it is in the same
        # directory as current Python executable.
        # sys.executable can be None or an empty string.
        if not sys.executable:
            return (None, None, None)
        crashtracker_exe = os.path.join(os.path.dirname(sys.executable), "crashtracker_exe")
        if not os.path.exists(crashtracker_exe) or not os.access(crashtracker_exe, os.X_OK):
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
        stracktrace_resolver = StacktraceCollection.EnabledWithInprocessSymbols

    # Create crashtracker configuration
    config = CrashtrackerConfiguration(
        [],  # additional_files
        crashtracker_config.create_alt_stack,
        crashtracker_config.use_alt_stack,
        crashtracker_config.timeout_ms,
        stacktrace_resolver,
        crashtracker_config.debug_url or agent.get_trace_url(),
        None,  # unix_socket_path
    )

    # Create crashtracker receiver configuration
    receiver_config = CrashtrackerReceiverConfig(
        [],  # args
        {},  # env
        crashtracker_exe,
        crashtracker_config.stderr_filename,
        crashtracker_config.stdout_filename,
    )

    tags = _get_tags(additional_tags)

    metadata = Metadata("dd-trace-py", version.get_version(), "python", tags)

    return config, receiver_config, metadata


def is_started() -> bool:
    return crashtracker_status() == CrashtrackerStatus.Initialized


def start(additional_tags: Optional[Dict[str, str]] = None) -> bool:
    if not crashtracker_config.enabled:
        return False

    config, receiver_config, metadata = _get_args(additional_tags)
    if config is None or receiver_config is None or metadata is None:
        print("Failed to start crashtracker: failed to construct crashtracker configuration")
        return False

    try:
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
