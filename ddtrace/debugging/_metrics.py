from ddtrace.internal.metrics import DogStatsdClient
from ddtrace.internal.metrics import InstrumentationTelemetryMetricsClient
from ddtrace.internal.metrics import Metrics
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


# Debugger metrics (reported via instrumentation telemetry)
metrics = Metrics(client=InstrumentationTelemetryMetricsClient(TELEMETRY_NAMESPACE.DEBUGGER))

# Metric probe metrics (always enabled)
probe_metrics = Metrics(client=DogStatsdClient(namespace="debugger.metric"))
probe_metrics.enable()
