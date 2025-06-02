from enum import Enum


class TELEMETRY_NAMESPACE(Enum):
    TRACERS = "tracers"
    APPSEC = "appsec"
    IAST = "iast"
    CIVISIBILITY = "civisibility"
    MLOBS = "mlobs"
    DD_TRACE_API = "ddtraceapi"
    PROFILER = "profiler"


TELEMETRY_TYPE_GENERATE_METRICS = "generate-metrics"
TELEMETRY_TYPE_DISTRIBUTION = "distributions"
TELEMETRY_TYPE_LOGS = "logs"


class TELEMETRY_LOG_LEVEL(Enum):
    DEBUG = "DEBUG"
    WARNING = "WARN"
    ERROR = "ERROR"


class TELEMETRY_APM_PRODUCT(str, Enum):
    LLMOBS = "mlobs"
    DYNAMIC_INSTRUMENTATION = "dynamic_instrumentation"
    PROFILER = "profiler"
    APPSEC = "appsec"
