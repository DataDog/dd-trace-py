from bm import Scenario

from ddtrace.internal.telemetry.metrics_namespaces import add_metric

class TelemetryAddMetric(Scenario):
    """
    This scenario checks to see if there's an impact on sending metrics via instrumentation telemetry
    """
    count: int
    def run(self):
        def _(loops):
            for _ in range(loops):
                add_metric("test", "metric", count, {"integration_name": "somevalue"})

        yield _