from bm import Scenario

from ddtrace.internal.telemetry.metrics_namespaces import MetricNamespace
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.metrics import CountMetric

class TelemetryAddMetric(Scenario):
    """
    This scenario checks to see if there's an impact on sending metrics via instrumentation telemetry
    """
    count: int
    runs: int
    def run(self):
        def _(loops):
            for _ in range(loops):
                for i in range(self.runs):
                    metricnamespace = MetricNamespace()
                    metricnamespace.add_metric(CountMetric, TELEMETRY_NAMESPACE.TRACERS, "metric", self.count, tags=(("integration_name","somevalue"),))
                    metricnamespace.flush()

        yield _