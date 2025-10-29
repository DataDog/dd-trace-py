import itertools
import math
import os
import sys
import typing as t

from ddtrace.ext.git import COMMIT_SHA
from ddtrace.ext.git import MAIN_PACKAGE
from ddtrace.ext.git import REPOSITORY_URL
from ddtrace.internal import compat
from ddtrace.internal import gitmetadata
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import report_configuration
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.settings._core import DDConfig


logger = get_logger(__name__)


def _derive_default_heap_sample_size(heap_config, default_heap_sample_size=1024 * 1024):
    # type: (ProfilingConfigHeap, int) -> int
    heap_sample_size = heap_config._sample_size
    if heap_sample_size is not None:
        return heap_sample_size

    if not heap_config.enabled:
        return 0

    try:
        from ddtrace.vendor import psutil

        total_mem = psutil.swap_memory().total + psutil.virtual_memory().total
    except Exception:
        logger.warning(
            "Unable to get total memory available, using default value of %d KB",
            default_heap_sample_size / 1024,
            exc_info=True,
        )
        return default_heap_sample_size

    # This is TRACEBACK_ARRAY_MAX_COUNT
    max_samples = 2**16

    return int(max(math.ceil(total_mem / max_samples), default_heap_sample_size))


def _check_for_ddup_available():
    # NB: importing ddup module results in importing _ddup.so file which could
    # raise an Exception within the ddup module, but we catch it there and
    # we don't propagate up to here. And regardless of whether ddup is available,
    # failure_msg and is_available are set to appropriate values.
    from ddtrace.internal.datadog.profiling import ddup

    return (ddup.failure_msg, ddup.is_available)


def _check_for_stack_v2_available():
    # NB: ditto for stack_v2 module as ddup.
    from ddtrace.internal.datadog.profiling import stack_v2

    return (stack_v2.failure_msg, stack_v2.is_available)


def _parse_profiling_enabled(raw: str) -> bool:
    if sys.version_info >= (3, 14):
        return False

    # Try to derive whether we're enabled via DD_INJECTION_ENABLED
    # - Are we injected (DD_INJECTION_ENABLED set)
    # - Is profiling enabled ("profiler" in the list)
    if os.environ.get("DD_INJECTION_ENABLED") is not None:
        for tok in os.environ.get("DD_INJECTION_ENABLED", "").split(","):
            if tok.strip().lower() == "profiler":
                return True

    # This is the normal check
    raw_lc = raw.lower()
    if raw_lc in ("1", "true", "yes", "on", "auto"):
        return True

    # If it wasn't enabled, then disable it
    return False


def _update_git_metadata_tags(tags):
    """
    Update profiler tags with git metadata
    """
    # clean tags, because values will be combined and inserted back in the same way as for tracer
    gitmetadata.clean_tags(tags)
    repository_url, commit_sha, main_package = gitmetadata.get_git_tags()
    if repository_url:
        tags[REPOSITORY_URL] = repository_url
    if commit_sha:
        tags[COMMIT_SHA] = commit_sha
    if main_package:
        tags[MAIN_PACKAGE] = main_package
    return tags


def _enrich_tags(tags) -> t.Dict[str, str]:
    tags = {
        k: compat.ensure_text(v, "utf-8")
        for k, v in itertools.chain(
            _update_git_metadata_tags(parse_tags_str(os.environ.get("DD_TAGS"))).items(),
            tags.items(),
        )
    }

    return tags


class ProfilingConfig(DDConfig):
    __prefix__ = "dd.profiling"

    # Nested configs that need metadata synchronization in reload_from_env()
    # Update this list when adding new nested configs via .include()
    _NESTED_CONFIGS = ["stack", "lock", "memory", "heap", "pytorch"]

    # Note that the parser here has a side-effect, since SSI has changed the once-truthy value of the envvar to
    # truthy + "auto", which has a special meaning.
    enabled = DDConfig.v(
        bool,
        "enabled",
        parser=_parse_profiling_enabled,
        default=False,
        help_type="Boolean",
        help="Enable Datadog profiling when using ``ddtrace-run``",
    )

    def reload_from_env(self):
        """Reload configuration from environment variables in-place.

        This method updates the existing config object with fresh values from
        environment variables, preserving all existing references to this object.
        This is critical for maintaining consistency across modules that have
        already imported this config instance.
        """
        # Create a temporary new config to read fresh environment variables
        new_config = ProfilingConfig()

        # Copy all configuration values using the DDConfig items iterator
        # This properly handles nested configs (stack, lock, memory, heap, pytorch)
        for name, env_var in type(self).items(recursive=True):
            # Get the value from the new config
            new_value = new_config
            for part in name.split("."):
                new_value = getattr(new_value, part)

            # Set the value on self
            current = self
            parts = name.split(".")
            for part in parts[:-1]:
                current = getattr(current, part)
            setattr(current, parts[-1], new_value)

        # Explicitly update derived fields that depend on other config values
        # These are defined with DDConfig.d() and need to be recalculated
        # stack.v2_enabled depends on stack._v2_enabled and stack.enabled
        self.stack.v2_enabled = new_config.stack.v2_enabled
        # heap.sample_size is derived from heap._sample_size and system memory
        self.heap.sample_size = new_config.heap.sample_size

        # Update internal tracking attributes for root config
        self._value_source = new_config._value_source
        self.config_id = new_config.config_id

        # Update internal tracking attributes for nested configs
        # This ensures value_source() and config_id work correctly for nested settings
        for nested_name in self._NESTED_CONFIGS:
            nested_config = getattr(self, nested_name)
            new_nested_config = getattr(new_config, nested_name)
            nested_config._value_source = new_nested_config._value_source
            nested_config.config_id = new_nested_config.config_id

    agentless = DDConfig.v(
        bool,
        "agentless",
        default=False,
        help_type="Boolean",
        help="",
    )

    code_provenance = DDConfig.v(
        bool,
        "enable_code_provenance",
        default=True,
        help_type="Boolean",
        help="Whether to enable code provenance",
    )

    endpoint_collection = DDConfig.v(
        bool,
        "endpoint_collection_enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the endpoint data collection in profiles",
    )

    output_pprof = DDConfig.v(
        t.Optional[str],
        "output_pprof",
        default=None,
        help_type="String",
        help="",
    )

    upload_interval = DDConfig.v(
        float,
        "upload_interval",
        default=60.0,
        help_type="Float",
        help="The interval in seconds to wait before flushing out recorded events",
    )

    capture_pct = DDConfig.v(
        float,
        "capture_pct",
        default=1.0,
        help_type="Float",
        help="The percentage of events that should be captured (e.g. memory "
        "allocation). Greater values reduce the program execution speed. Must be "
        "greater than 0 lesser or equal to 100",
    )

    max_frames = DDConfig.v(
        int,
        "max_frames",
        default=64,
        help_type="Integer",
        help="The maximum number of frames to capture in stack execution tracing",
    )

    ignore_profiler = DDConfig.v(
        bool,
        "ignore_profiler",
        default=False,
        help_type="Boolean",
        help="**Deprecated**: whether to ignore the profiler in the generated data",
    )

    max_time_usage_pct = DDConfig.v(
        float,
        "max_time_usage_pct",
        default=1.0,
        help_type="Float",
        help="The percentage of maximum time the stack profiler can use when computing "
        "statistics. Must be greater than 0 and lesser or equal to 100",
    )

    api_timeout = DDConfig.v(
        float,
        "api_timeout",
        default=10.0,
        help_type="Float",
        help="The timeout in seconds before dropping events if the HTTP API does not reply",
    )

    timeline_enabled = DDConfig.v(
        bool,
        "timeline_enabled",
        default=True,
        help_type="Boolean",
        help="Whether to add timestamp information to captured samples.  Adds a small amount of "
        "overhead to the profiler, but enables the use of the Timeline view in the UI.",
    )

    tags = DDConfig.v(
        dict,
        "tags",
        parser=parse_tags_str,
        default={},
        help_type="Mapping",
        help="The tags to apply to uploaded profile. Must be a list in the ``key1:value,key2:value2`` format",
    )

    enable_asserts = DDConfig.v(
        bool,
        "enable_asserts",
        default=False,
        help_type="Boolean",
        help="Whether to enable debug assertions in the profiler code",
    )

    sample_pool_capacity = DDConfig.v(
        int,
        "sample_pool_capacity",
        default=4,
        help_type="Integer",
        help="The number of Sample objects to keep in the pool for reuse. "
        "Increasing this can reduce the overhead from frequently allocating "
        "and deallocating Sample objects.",
    )


class ProfilingConfigStack(DDConfig):
    __item__ = __prefix__ = "stack"

    enabled = DDConfig.v(
        bool,
        "enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the stack profiler",
    )

    _v2_enabled = DDConfig.v(
        bool,
        "v2_enabled",
        # Not yet supported on 3.14
        default=sys.version_info < (3, 14),
        help_type="Boolean",
        help="Whether to enable the v2 stack profiler. Also enables the libdatadog collector.",
    )

    # V2 can't be enabled if stack collection is disabled or if pre-requisites are not met
    v2_enabled = DDConfig.d(bool, lambda c: c._v2_enabled and c.enabled)

    v2_adaptive_sampling = DDConfig.v(
        bool,
        "v2.adaptive_sampling.enabled",
        default=True,
        help_type="Boolean",
        private=True,
    )


class ProfilingConfigLock(DDConfig):
    __item__ = __prefix__ = "lock"

    enabled = DDConfig.v(
        bool,
        "enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the lock profiler",
    )

    name_inspect_dir = DDConfig.v(
        bool,
        "name_inspect_dir",
        default=True,
        help_type="Boolean",
        help="Whether to inspect the ``dir()`` of local and global variables to find the name of the lock. "
        "With this enabled, the profiler finds the name of locks that are attributes of an object.",
    )


class ProfilingConfigMemory(DDConfig):
    __item__ = __prefix__ = "memory"

    enabled = DDConfig.v(
        bool,
        "enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the memory profiler",
    )

    events_buffer = DDConfig.v(
        int,
        "events_buffer",
        default=16,
        help_type="Integer",
        help="",
    )


class ProfilingConfigHeap(DDConfig):
    __item__ = __prefix__ = "heap"

    enabled = DDConfig.v(
        bool,
        "enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the heap memory profiler",
    )

    _sample_size = DDConfig.v(
        t.Optional[int],
        "sample_size",
        default=None,
        help_type="Integer",
        help="Average number of bytes allocated between memory profiler samples",
    )
    sample_size = DDConfig.d(int, _derive_default_heap_sample_size)


class ProfilingConfigPytorch(DDConfig):
    __item__ = __prefix__ = "pytorch"

    enabled = DDConfig.v(
        bool,
        "enabled",
        default=False,
        help_type="Boolean",
        help="Whether to enable the PyTorch profiler",
    )

    events_limit = DDConfig.v(
        int,
        "events_limit",
        default=1_000_000,
        help_type="Integer",
        help="How many events the PyTorch profiler records each collection",
    )


# Include all the sub-configs
ProfilingConfig.include(ProfilingConfigStack, namespace="stack")
ProfilingConfig.include(ProfilingConfigLock, namespace="lock")
ProfilingConfig.include(ProfilingConfigMemory, namespace="memory")
ProfilingConfig.include(ProfilingConfigHeap, namespace="heap")
ProfilingConfig.include(ProfilingConfigPytorch, namespace="pytorch")

config = ProfilingConfig()
report_configuration(config)

ddup_failure_msg, ddup_is_available = _check_for_ddup_available()

# We need to check if ddup is available, and turn off profiling if it is not.
if not ddup_is_available:
    # We know it is not supported on 3.14, so don't report the error, but still disable
    if sys.version_info < (3, 14):
        msg = ddup_failure_msg or "libdd not available"
        logger.warning("Failed to load ddup module (%s), disabling profiling", msg)
        telemetry_writer.add_log(
            TELEMETRY_LOG_LEVEL.ERROR,
            "Failed to load ddup module (%s), disabling profiling" % ddup_failure_msg,
        )
    config.enabled = False

# We also need to check if stack_v2 module is available, and turn if off
# if it s not.
stack_v2_failure_msg, stack_v2_is_available = _check_for_stack_v2_available()
if config.stack.v2_enabled and not stack_v2_is_available:
    msg = stack_v2_failure_msg or "stack_v2 not available"
    logger.warning("Failed to load stack_v2 module (%s), falling back to v1 stack sampler", msg)
    telemetry_writer.add_log(
        TELEMETRY_LOG_LEVEL.ERROR,
        "Failed to load stack_v2 module (%s), falling back to v1 stack sampler" % msg,
    )
    config.stack.v2_enabled = False

# Enrich tags with git metadata and DD_TAGS
config.tags = _enrich_tags(config.tags)


def config_str(config):
    configured_features = []
    if config.stack.enabled:
        if config.stack.v2_enabled:
            configured_features.append("stack_v2")
        else:
            configured_features.append("stack")
    if config.lock.enabled:
        configured_features.append("lock")
    if config.memory.enabled:
        configured_features.append("mem")
    if config.heap.sample_size > 0:
        configured_features.append("heap")
    if config.pytorch.enabled:
        configured_features.append("pytorch")
    configured_features.append("exp_dd")
    configured_features.append("CAP" + str(config.capture_pct))
    configured_features.append("MAXF" + str(config.max_frames))
    return "_".join(configured_features)


def _reload_config_after_fork():
    """Reload configuration after fork to respect child process environment.

    This is critical for multi-worker servers like Gunicorn where:
    1. Parent process may have DD_PROFILING_ENABLED=true
    2. Child workers want DD_PROFILING_ENABLED=false
    3. Environment variables are changed after fork

    The config is re-read from environment variables in the child process.
    Instead of creating a new instance, we reload the existing instance in-place
    to preserve all existing references to the config object.
    """
    global config

    # Store old value for logging
    old_enabled = config.enabled

    # Reload config in-place from environment variables
    # This preserves all existing references to the config object
    config.reload_from_env()

    # Re-check ddup availability
    global ddup_is_available
    if not ddup_is_available:
        config.enabled = False

    # Re-check stack_v2 availability
    global stack_v2_is_available
    if config.stack.v2_enabled and not stack_v2_is_available:
        config.stack.v2_enabled = False

    # Enrich tags again
    config.tags = _enrich_tags(config.tags)

    if old_enabled != config.enabled:
        logger.debug(
            "Profiling config changed after fork (PID=%d): enabled=%s -> %s",
            os.getpid(),
            old_enabled,
            config.enabled,
        )


# Register fork hook (executed before profiler restart hooks)
from ddtrace.internal import forksafe

forksafe.register(_reload_config_after_fork)
