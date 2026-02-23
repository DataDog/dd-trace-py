import importlib.util
import os
import platform
import sys
from typing import Optional

from ddtrace import config
from ddtrace import version
from ddtrace.internal import forksafe
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.logger import get_logger
from ddtrace.internal import process_tags
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings.crashtracker import config as crashtracker_config
from ddtrace.internal.settings.profiling import config as profiling_config
from ddtrace.internal.settings.profiling import config_str


log = get_logger(__name__)


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


def _get_tags(additional_tags: Optional[dict[str, str]]) -> dict[str, str]:
    tags = {
        "language": "python",
        "runtime": "CPython",
        "is_crash": "true",
        "severity": "crash",
    }

    process_tags._set_globals()

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
    library_version = version.__version__
    if library_version:
        tags["library_version"] = library_version
    if process_tags.process_tags:
        tags["process_tags"] = process_tags.process_tags

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


def _get_args(additional_tags: Optional[dict[str, str]]):
    # Instead of searching PATH for the receiver binary, invoke the receiver script
    # directly with the current Python interpreter. This is more reliable and doesn't
    # depend on PATH configuration.
    python_executable = sys.executable

    # Get the path to the receiver script module
    spec = importlib.util.find_spec("ddtrace.commands._dd_crashtracker_receiver")
    if spec is None:
        log.error("Failed to locate _dd_crashtracker_receiver module")
        return (None, None, None)

    receiver_script_path = spec.origin
    if not receiver_script_path:
        log.error("Failed to resolve path to _dd_crashtracker_receiver module")
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
        log.error("Invalid stacktrace_resolver value: %s", crashtracker_config.stacktrace_resolver)
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

    receiver_env = {}

    # Don't pass all env vars to the receiver process, because there are
    # conflicts with export location derivation
    crashtracking_enabled = os.environ.get("DD_CRASHTRACKING_ERRORS_INTAKE_ENABLED")
    if crashtracking_enabled is not None:
        receiver_env["DD_CRASHTRACKING_ERRORS_INTAKE_ENABLED"] = crashtracking_enabled

    # Crashtracker is supported only on Linux and macOS, so we only need to inherit
    # these library path environment variables.
    # setup.py:269
    inherited_env_vars = [
        "LD_LIBRARY_PATH",  # for loading native ext (Linux)
        "DYLD_LIBRARY_PATH",  # for loading native ext (macOS)
        "PYTHONPATH",  # for loading Python, for the receiver script
    ]
    for env_var in inherited_env_vars:
        env_value = os.environ.get(env_var)
        if env_value is not None:
            receiver_env[env_var] = env_value

    # This is equivalent to: python /path/to/_dd_crashtracker_receiver.py
    receiver_config = CrashtrackerReceiverConfig(
        [python_executable, receiver_script_path],  # args: [program_name, script_path]
        receiver_env,
        python_executable,  # path_to_receiver_binary: use current Python interpreter
        crashtracker_config.stderr_filename,
        crashtracker_config.stdout_filename,
    )

    tags = _get_tags(additional_tags)

    metadata = CrashtrackerMetadata("dd-trace-py", version.__version__, "python", tags)

    return config, receiver_config, metadata


def is_started() -> bool:
    if not is_available:
        return False
    return crashtracker_status() == CrashtrackerStatus.Initialized


def start(additional_tags: Optional[dict[str, str]] = None) -> bool:
    if not is_available:
        return False
    if not crashtracker_config.enabled:
        return False

    try:
        config, receiver_config, metadata = _get_args(additional_tags)
        if config is None or receiver_config is None or metadata is None:
            log.error("Failed to start crashtracker: failed to construct crashtracker configuration")
            return False

        # TODO: Add this back in post Code Freeze (need to update config registry)
        # crashtracker_init(
        #     config, receiver_config, metadata, emit_runtime_stacks=crashtracker_config.emit_runtime_stacks
        # )

        crashtracker_init(config, receiver_config, metadata)

        def crashtracker_fork_handler():
            # We recreate the args here mainly to pass updated runtime_id after
            # fork
            config, receiver_config, metadata = _get_args(additional_tags)
            if config is None or receiver_config is None or metadata is None:
                log.error("Failed to restart crashtracker after fork: failed to construct crashtracker configuration")
                return
            crashtracker_on_fork(config, receiver_config, metadata)

        forksafe.register(crashtracker_fork_handler)
    except Exception:
        log.exception("Failed to start crashtracker")
        return False
    return True
