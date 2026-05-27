GC_COUNT_GEN0 = "runtime.python.gc.count.gen0"
GC_COUNT_GEN1 = "runtime.python.gc.count.gen1"
GC_COUNT_GEN2 = "runtime.python.gc.count.gen2"

THREAD_COUNT = "runtime.python.thread_count"
MEM_RSS = "runtime.python.mem.rss"
# `runtime.python.cpu.time.sys` metric is used to auto-enable runtime metrics dashboards in DD backend
CPU_TIME_SYS = "runtime.python.cpu.time.sys"
CPU_TIME_USER = "runtime.python.cpu.time.user"
CPU_PERCENT = "runtime.python.cpu.percent"
CTX_SWITCH_VOLUNTARY = "runtime.python.cpu.ctx_switch.voluntary"
CTX_SWITCH_INVOLUNTARY = "runtime.python.cpu.ctx_switch.involuntary"

PROFILER_SAMPLE_COUNT = "runtime.python.profiler.sample_count"
PROFILER_SAMPLING_EVENT_COUNT = "runtime.python.profiler.sampling_event_count"
PROFILER_COPY_MEMORY_ERROR_COUNT = "runtime.python.profiler.copy_memory_error_count"
PROFILER_SAMPLE_CAPTURE_CPU_TIME_US = "runtime.python.profiler.sample_capture_cpu_time_us"
PROFILER_SAMPLING_INTERVAL_US = "runtime.python.profiler.sampling_interval_us"
PROFILER_ASYNCIO_TASK_COUNT = "runtime.python.profiler.asyncio_task_count"
PROFILER_GREENLET_COUNT = "runtime.python.profiler.greenlet_count"
PROFILER_HEAP_TRACKER_COUNT = "runtime.python.profiler.heap_tracker_count"
PROFILER_STRING_TABLE_COUNT = "runtime.python.profiler.string_table_count"
PROFILER_STRING_TABLE_EPHEMERAL_COUNT = "runtime.python.profiler.string_table_ephemeral_count"
PROFILER_FAST_COPY_MEMORY_ENABLED = "runtime.python.profiler.fast_copy_memory_enabled"

GC_RUNTIME_METRICS = set([GC_COUNT_GEN0, GC_COUNT_GEN1, GC_COUNT_GEN2])

PSUTIL_RUNTIME_METRICS = set(
    [THREAD_COUNT, MEM_RSS, CTX_SWITCH_VOLUNTARY, CTX_SWITCH_INVOLUNTARY, CPU_TIME_SYS, CPU_TIME_USER, CPU_PERCENT]
)

PROFILER_RUNTIME_METRICS = set(
    [
        PROFILER_SAMPLE_COUNT,
        PROFILER_SAMPLING_EVENT_COUNT,
        PROFILER_COPY_MEMORY_ERROR_COUNT,
        PROFILER_SAMPLE_CAPTURE_CPU_TIME_US,
        PROFILER_SAMPLING_INTERVAL_US,
        PROFILER_ASYNCIO_TASK_COUNT,
        PROFILER_GREENLET_COUNT,
        PROFILER_HEAP_TRACKER_COUNT,
        PROFILER_STRING_TABLE_COUNT,
        PROFILER_STRING_TABLE_EPHEMERAL_COUNT,
        PROFILER_FAST_COPY_MEMORY_ENABLED,
    ]
)

DEFAULT_RUNTIME_METRICS = GC_RUNTIME_METRICS | PSUTIL_RUNTIME_METRICS | PROFILER_RUNTIME_METRICS
DEFAULT_RUNTIME_METRICS_INTERVAL = 10

SERVICE = "service"
ENV = "env"
LANG_INTERPRETER = "lang_interpreter"
LANG_VERSION = "lang_version"
LANG = "lang"
TRACER_VERSION = "tracer_version"
