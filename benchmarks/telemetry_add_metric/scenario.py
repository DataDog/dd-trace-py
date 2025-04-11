from bm import Scenario

from ddtrace.internal.telemetry.metrics_namespaces import MetricNamespace
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.metrics import CountMetric


class TelemetryAddMetric(Scenario):
    """
    This scenario checks to see if there's an impact on sending metrics via instrumentation telemetry
    """

    runs: int
    flush: bool
    batches: int

    def run(self):
        def _(loops):
            for _ in range(loops):
                # Start the test when MetricNamespace is created
                metricnamespace = MetricNamespace()
                for i in range(self.runs):
                    for j in range(self.batches):
                        metricnamespace.add_metric(
                            CountMetric,
                            TELEMETRY_NAMESPACE.TRACERS,
                            str(j),
                            10,
                            tags=(("integration_name", "somevalue"),),
                        )

                # Make flush optional
                if self.flush:
                    metricnamespace.flush()

        yield _
