from typing import Dict
from typing import Optional

from ddtrace.internal.settings._core import DDConfig

class ProfilingConfig(DDConfig):
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
    tags: Dict[str, str]
    enable_asserts: bool
    sample_pool_capacity: int
    stack: ProfilingConfigStack
    lock: ProfilingConfigLock
    memory: ProfilingConfigMemory
    heap: ProfilingConfigHeap
    pytorch: ProfilingConfigPytorch

class ProfilingConfigStack(DDConfig):
    enabled: bool
    v2_adaptive_sampling: bool

class ProfilingConfigLock(DDConfig):
    enabled: bool
    name_inspect_dir: bool

class ProfilingConfigMemory(DDConfig):
    enabled: bool
    events_buffer: int

class ProfilingConfigHeap(DDConfig):
    enabled: bool
    _sample_size: Optional[int]
    sample_size: int

class ProfilingConfigPytorch(DDConfig):
    enabled: bool
    events_limit: int

config: ProfilingConfig
ddup_failure_msg: Optional[str]
ddup_is_available: bool
stack_v2_failure_msg: Optional[str]
stack_v2_is_available: bool

def config_str(config: ProfilingConfig) -> str: ...
