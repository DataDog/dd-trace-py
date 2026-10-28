from typing import Any
from typing import Optional

from ddtrace.internal.settings._core import DDConfig

# Note: the stubs for ProfilingConfig, ProfilingConfigStack, etc.
# type the parsed attribute as their own type. This is incorrect in
# practice, because parsed only contains the "top-level" value, i.e.
# profiling_config.parsed.stack does not exist.
# The goal of those stubs is to have sensible typing for configuration,
# so it is fine to leave it like that for the time being.

class ProfilingConfig(DDConfig):
    parsed: ProfilingConfig

    enabled: bool
    agentless: bool
    code_provenance: bool
    endpoint_collection: bool
    output_pprof: Optional[str]
    upload_interval: float
    capture_pct: float
    max_frames: int
    ignore_profiler: bool
    max_time_usage_pct: float
    api_timeout_ms: int
    timeline_enabled: bool
    tags: dict[str, str]
    enable_asserts: bool
    sample_pool_capacity: int
    stack: ProfilingConfigStack
    lock: ProfilingConfigLock
    memory: ProfilingConfigMemory
    heap: ProfilingConfigHeap
    native_heap: ProfilingConfigNativeHeap
    pytorch: ProfilingConfigPytorch
    exception: ProfilingConfigException
    install: bool

    def dump_settings(self) -> dict[str, Any]: ...

class ProfilingConfigStack(DDConfig):
    parsed: ProfilingConfigStack

    enabled: bool
    adaptive_sampling: bool
    adaptive_sampling_target_overhead: float
    adaptive_sampling_max_interval: int
    adaptive_sampling_baseline: float
    adaptive_sampling_p_stable_window_s: int
    adaptive_sampling_p_stable_percentile: float
    max_threads: int
    max_tasks: int
    native_frames: bool
    gc_enabled: bool
    fast_copy: bool

class ProfilingConfigLock(DDConfig):
    parsed: ProfilingConfigLock

    enabled: bool
    name_inspect_dir: bool
    exclude_modules: frozenset[str]

class ProfilingConfigMemory(DDConfig):
    parsed: ProfilingConfigMemory

    enabled: bool
    events_buffer: int
    mem_domain_enabled: bool

class ProfilingConfigHeap(DDConfig):
    parsed: ProfilingConfigHeap

    enabled: bool
    _sample_size: Optional[int]
    sample_size: int

class ProfilingConfigNativeHeap(DDConfig):
    parsed: ProfilingConfigNativeHeap

    enabled: bool

class ProfilingConfigPytorch(DDConfig):
    parsed: ProfilingConfigPytorch

    enabled: bool
    events_limit: int
    max_frames: int

class ProfilingConfigException(DDConfig):
    parsed: ProfilingConfigException

    enabled: bool
    sampling_interval: int
    collect_message: bool

config: ProfilingConfig
ddup_failure_msg: Optional[str]
ddup_is_available: bool
stack_v2_failure_msg: Optional[str]
stack_v2_is_available: bool
exception_failure_msg: str
exception_is_available: bool

def config_str(config: ProfilingConfig) -> str: ...
def _is_python_embedded() -> bool: ...
def _is_platform_supported() -> tuple[str, bool]: ...
