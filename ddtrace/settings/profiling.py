import math
import typing as t

from envier import En

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import parse_tags_str


logger = get_logger(__name__)


# Stash the reason why a transitive dependency failed to load; since we try to load things safely in order to guide
# configuration, these errors won't bubble up naturally.  All of these components should use the same pattern
# in order to guarantee uniformity.
ddup_failure_msg = ""
stack_v2_failure_msg = ""


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
    global ddup_failure_msg
    ddup_is_available = False
    try:
        from ddtrace.internal.datadog.profiling import ddup

        ddup_is_available = ddup.is_available
        ddup_failure_msg = ddup.failure_msg
    except Exception:
        pass  # nosec
    return ddup_is_available


def _check_for_stack_v2_available():
    global stack_v2_failure_msg
    stack_v2_is_available = False

    # stack_v2 will use libdd; in order to prevent two separate collectors from running, it then needs to force
    # libdd to be enabled as well; that means it depends on the libdd interface (ddup)
    if not _check_for_ddup_available():
        return False

    try:
        from ddtrace.internal.datadog.profiling import stack_v2

        stack_v2_is_available = stack_v2.is_available
        stack_v2_failure_msg = stack_v2.failure_msg
    except Exception:
        pass  # nosec
    return stack_v2_is_available


def _is_libdd_required(config):
    return config.stack.v2_enabled or config.export._libdd_enabled


class ProfilingConfig(En):
    __prefix__ = "dd.profiling"

    enabled = En.v(
        bool,
        "enabled",
        default=False,
        help_type="Boolean",
        help="Enable Datadog profiling when using ``ddtrace-run``",
    )

    agentless = En.v(
        bool,
        "agentless",
        default=False,
        help_type="Boolean",
        help="",
    )

    code_provenance = En.v(
        bool,
        "enable_code_provenance",
        default=True,
        help_type="Boolean",
        help="Whether to enable code provenance",
    )

    endpoint_collection = En.v(
        bool,
        "endpoint_collection_enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the endpoint data collection in profiles",
    )

    output_pprof = En.v(
        t.Optional[str],
        "output_pprof",
        default=None,
        help_type="String",
        help="",
    )

    max_events = En.v(
        int,
        "max_events",
        default=16384,
        help_type="Integer",
        help="",
    )

    upload_interval = En.v(
        float,
        "upload_interval",
        default=60.0,
        help_type="Float",
        help="The interval in seconds to wait before flushing out recorded events",
    )

    capture_pct = En.v(
        float,
        "capture_pct",
        default=1.0,
        help_type="Float",
        help="The percentage of events that should be captured (e.g. memory "
        "allocation). Greater values reduce the program execution speed. Must be "
        "greater than 0 lesser or equal to 100",
    )

    max_frames = En.v(
        int,
        "max_frames",
        default=64,
        help_type="Integer",
        help="The maximum number of frames to capture in stack execution tracing",
    )

    ignore_profiler = En.v(
        bool,
        "ignore_profiler",
        default=False,
        help_type="Boolean",
        help="**Deprecated**: whether to ignore the profiler in the generated data",
    )

    max_time_usage_pct = En.v(
        float,
        "max_time_usage_pct",
        default=1.0,
        help_type="Float",
        help="The percentage of maximum time the stack profiler can use when computing "
        "statistics. Must be greater than 0 and lesser or equal to 100",
    )

    api_timeout = En.v(
        float,
        "api_timeout",
        default=10.0,
        help_type="Float",
        help="The timeout in seconds before dropping events if the HTTP API does not reply",
    )

    timeline_enabled = En.v(
        bool,
        "timeline_enabled",
        default=False,
        help_type="Boolean",
        help="Whether to add timestamp information to captured samples.  Adds a small amount of "
        "overhead to the profiler, but enables the use of the Timeline view in the UI.",
    )

    tags = En.v(
        dict,
        "tags",
        parser=parse_tags_str,
        default={},
        help_type="Mapping",
        help="The tags to apply to uploaded profile. Must be a list in the ``key1:value,key2:value2`` format",
    )

    enable_asserts = En.v(
        bool,
        "enable_asserts",
        default=False,
        help_type="Boolean",
        help="Whether to enable debug assertions in the profiler code",
    )


class ProfilingConfigStack(En):
    __item__ = __prefix__ = "stack"

    enabled = En.v(
        bool,
        "enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the stack profiler",
    )

    _v2_enabled = En.v(
        bool,
        "v2_enabled",
        default=False,
        help_type="Boolean",
        help="Whether to enable the v2 stack profiler. Also enables the libdatadog collector.",
    )

    # V2 can't be enabled if stack collection is disabled or if pre-requisites are not met
    v2_enabled = En.d(bool, lambda c: _check_for_stack_v2_available() and c._v2_enabled and c.enabled)


class ProfilingConfigLock(En):
    __item__ = __prefix__ = "lock"

    enabled = En.v(
        bool,
        "enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the lock profiler",
    )

    name_inspect_dir = En.v(
        bool,
        "name_inspect_dir",
        default=True,
        help_type="Boolean",
        help="Whether to inspect the ``dir()`` of local and global variables to find the name of the lock. "
        "With this enabled, the profiler finds the name of locks that are attributes of an object.",
    )


class ProfilingConfigMemory(En):
    __item__ = __prefix__ = "memory"

    enabled = En.v(
        bool,
        "enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the memory profiler",
    )

    events_buffer = En.v(
        int,
        "events_buffer",
        default=16,
        help_type="Integer",
        help="",
    )


class ProfilingConfigHeap(En):
    __item__ = __prefix__ = "heap"

    enabled = En.v(
        bool,
        "enabled",
        default=True,
        help_type="Boolean",
        help="Whether to enable the heap memory profiler",
    )

    _sample_size = En.v(
        t.Optional[int],
        "sample_size",
        default=None,
        help_type="Integer",
        help="",
    )
    sample_size = En.d(int, _derive_default_heap_sample_size)


class ProfilingConfigExport(En):
    __item__ = __prefix__ = "export"

    _libdd_enabled = En.v(
        bool,
        "libdd_enabled",
        default=False,
        help_type="Boolean",
        help="Enables collection and export using a native exporter.  Can fallback to the pure-Python exporter.",
    )


# Include all the sub-configs
ProfilingConfig.include(ProfilingConfigStack, namespace="stack")
ProfilingConfig.include(ProfilingConfigLock, namespace="lock")
ProfilingConfig.include(ProfilingConfigMemory, namespace="memory")
ProfilingConfig.include(ProfilingConfigHeap, namespace="heap")
ProfilingConfig.include(ProfilingConfigExport, namespace="export")

config = ProfilingConfig()

# Force the enablement of libdd if the user requested a feature which requires it; otherwise the user has to manage
# configuration too intentionally and we'll need to change the API too much over time.
config.export.libdd_enabled = _is_libdd_required(config)

# Certain features depend on libdd being available.  If it isn't for some reason, those features cannot be enabled.
if config.stack.v2_enabled and not config.export.libdd_enabled:
    msg = ddup_failure_msg or "libdd not available"
    logger.warning("The v2 stack profiler cannot be used (%s)", msg)
    config.stack.v2_enabled = False

# Loading stack_v2 can fail for similar reasons
if config.stack.v2_enabled and not _check_for_stack_v2_available():
    msg = stack_v2_failure_msg or "stack_v2 not available"
    logger.warning("The v2 stack profiler cannot be used (%s)", msg)
    config.stack.v2_enabled = False
