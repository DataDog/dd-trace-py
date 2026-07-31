from enum import Enum
from typing import Optional


# Tag list form accepted by the telemetry metric helpers: ``((k, v), ...)`` or ``None``.
MetricTagType = Optional[tuple[tuple[str, str], ...]]


class TELEMETRY_NAMESPACE(Enum):
    # ``Enum.__hash__`` is a Python-level ``hash(self._name_)``, and every recorded metric hashes
    # its namespace as part of the metric-context cache key. Members are singletons, so identity
    # hashing super fast.
    __hash__ = object.__hash__

    TRACERS = "tracers"
    APPSEC = "appsec"
    IAST = "iast"
    CIVISIBILITY = "civisibility"
    MLOBS = "mlobs"
    DD_TRACE_API = "ddtraceapi"
    PROFILER = "profilers"
    DEBUGGER = "live_debugger"


class TELEMETRY_EVENT_TYPE(Enum):
    STARTED = "app-started"
    SHUTDOWN = "app-closing"
    HEARTBEAT = "app-heartbeat"
    EXTENDED_HEARTBEAT = "app-extended-heartbeat"
    DEPENDENCIES_LOADED = "app-dependencies-loaded"
    PRODUCT_CHANGE = "app-product-change"
    INTEGRATIONS_CHANGE = "app-integrations-change"
    ENDPOINTS = "app-endpoints"
    CLIENT_CONFIGURATION_CHANGE = "app-client-configuration-change"
    LOGS = "logs"
    METRICS = "generate-metrics"
    DISTRIBUTIONS = "distributions"
    MESSAGE_BATCH = "message-batch"


class TELEMETRY_LOG_LEVEL(Enum):
    DEBUG = "DEBUG"
    WARNING = "WARN"
    ERROR = "ERROR"


class TELEMETRY_APM_PRODUCT(str, Enum):
    LLMOBS = "mlobs"
    DYNAMIC_INSTRUMENTATION = "dynamic_instrumentation"
    PROFILER = "profiler"
    APPSEC = "appsec"
