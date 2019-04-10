GC_GEN0_COUNT = 'runtime.python.gc.gen0_count'
GC_GEN1_COUNT = 'runtime.python.gc.gen1_count'
GC_GEN2_COUNT = 'runtime.python.gc.gen2_count'

THREAD_COUNT = 'runtime.python.thread_count'
MEM_RSS = 'runtime.python.mem.rss'
CPU_TIME_SYS = 'runtime.python.cpu.time.sys'
CPU_TIME_USER = 'runtime.python.cpu.time.user'
CPU_PERCENT = 'runtime.python.cpu.percent'
CTX_SWITCH_VOLUNTARY = 'runtime.python.cpu.ctx_switch.voluntary'
CTX_SWITCH_INVOLUNTARY = 'runtime.python.cpu.ctx_switch.involuntary'

GC_RUNTIME_METRICS = set([
    GC_GEN0_COUNT,
    GC_GEN1_COUNT,
    GC_GEN2_COUNT,
])

PSUTIL_RUNTIME_METRICS = set([
    THREAD_COUNT,
    MEM_RSS,
    CTX_SWITCH_VOLUNTARY,
    CTX_SWITCH_INVOLUNTARY,
    CPU_TIME_SYS,
    CPU_TIME_USER,
    CPU_PERCENT,
])

DEFAULT_RUNTIME_METRICS = GC_RUNTIME_METRICS | PSUTIL_RUNTIME_METRICS

RUNTIME_ID = 'runtime.python.runtime-id'
SERVICE = 'runtime.python.service'
LANG_INTERPRETER = 'runtime.python.lang_interpreter'
LANG_VERSION = 'runtime.python.lang_version'

TRACER_TAGS = set([
    RUNTIME_ID,
    SERVICE,
])

PLATFORM_TAGS = set([
    LANG_INTERPRETER,
    LANG_VERSION
])

DEFAULT_RUNTIME_TAGS = TRACER_TAGS
