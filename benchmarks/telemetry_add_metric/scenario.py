import time

from bm import Scenario

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.metrics_namespaces import MetricNamespace


try:
    from ddtrace.internal.telemetry.metrics_namespaces import MetricType

    CountMetric = MetricType.COUNT
    DistributionMetric = MetricType.DISTRIBUTION
    GaugeMetric = MetricType.GAUGE
    RateMetric = MetricType.RATE
except ImportError:
    from ddtrace.internal.telemetry.metrics import CountMetric
    from ddtrace.internal.telemetry.metrics import DistributionMetric
    from ddtrace.internal.telemetry.metrics import GaugeMetric
    from ddtrace.internal.telemetry.metrics import RateMetric


def add_count_metric(namespace: MetricNamespace, name: str):
    namespace.add_metric(
        CountMetric,
        TELEMETRY_NAMESPACE.TRACERS,
        name,
        10,
        tags=(("integration_name", "somevalue"),),
    )


def add_gauge_metric(namespace: MetricNamespace, name: str):
    namespace.add_metric(
        GaugeMetric,
        TELEMETRY_NAMESPACE.TRACERS,
        name,
        10,
        tags=(("integration_name", "somevalue"),),
    )


def add_distribution_metric(namespace: MetricNamespace, name: str):
    namespace.add_metric(
        DistributionMetric,
        TELEMETRY_NAMESPACE.TRACERS,
        name,
        10,
        tags=(("integration_name", "somevalue"),),
    )


def add_rate_metric(namespace: MetricNamespace, name: str):
    namespace.add_metric(
        RateMetric,
        TELEMETRY_NAMESPACE.TRACERS,
        name,
        10,
        tags=(("integration_name", "somevalue"),),
    )


class TelemetryAddMetric(Scenario):
    """
    This scenario checks to see if there's an impact on sending metrics via instrumentation telemetry
    """

    metric_type: str
    num_metrics: int
    per_metric: int

    # Override `_pyperf` instead of `run` so we get better control over
    # how we run/time the scenario. This way we can ignore the creation time
    # of the MetricNamespace or other parts and only time the actual flush or
    # metrics adding
    def _pyperf(self, loops: int) -> float:
        if self.name.startswith("flush-"):
            return self.run_flush(loops)
        return self.run_add_metric(loops)

    def run_add_metric(self, loops: int) -> float:
        add_metric = None
        if self.metric_type == "count":
            add_metric = add_count_metric
        elif self.metric_type == "gauge":
            add_metric = add_gauge_metric
        elif self.metric_type == "distribution":
            add_metric = add_distribution_metric
        elif self.metric_type == "rate":
            add_metric = add_rate_metric
        else:
            raise ValueError(f"Scenario {self.name} has an unknown metric type: {self.metric_type}")

        total = 0
        metric_names = [str(i) for i in range(self.num_metrics)]
        for _ in range(loops):
            metricnamespace = MetricNamespace()
            st = time.perf_counter()
            for m in metric_names:
                for _ in range(self.per_metric):
                    add_metric(metricnamespace, m)

            total += time.perf_counter() - st
        return total

    def run_flush(self, loops: int) -> float:
        # Pool of metrics to use for adding
        metrics = (
            [(f"count-{i}", add_count_metric) for i in range(250)]
            + [(f"gauge-{i}", add_gauge_metric) for i in range(250)]
            + [(f"distribution-{i}", add_distribution_metric) for i in range(250)]
            + [(f"rate-{i}", add_rate_metric) for i in range(250)]
        )
        start = 0
        end = len(metrics)
        step = end // self.num_metrics
        total = 0.0

        # Pre-fill the dummy namespace with metrics
        dummy_namespace = MetricNamespace()
        for i in range(start, end, step):
            metrics[i][1](dummy_namespace, metrics[i][0])

        for _ in range(loops):
            metricnamespace = MetricNamespace()
            # Copy the dummy metrics to the new namespace, this saves time on adding metrics
            metricnamespace._metrics_data = dummy_namespace._metrics_data.copy()
            st = time.perf_counter()
            metricnamespace.flush()
            total += time.perf_counter() - st
        return total
