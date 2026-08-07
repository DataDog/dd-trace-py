import time

from bm import Scenario

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


# --- API detection --------------------------------------------------------
# On this branch the standalone Cython ``MetricNamespace`` aggregator was removed:
# metrics are recorded through the telemetry writer, which aggregates and flushes
# them in the native worker (off the Python hot path). On ``main`` the old
# ``MetricNamespace`` (with ``add_metric``/``flush``) still exists, so keep
# supporting it for the baseline side of the candidate-vs-baseline comparison.
try:
    from ddtrace.internal.telemetry.metrics_namespaces import MetricNamespace as _OldNamespace

    try:
        from ddtrace.internal.telemetry.metrics_namespaces import MetricType

        _OLD_TYPES = {
            "count": MetricType.COUNT,
            "gauge": MetricType.GAUGE,
            "distribution": MetricType.DISTRIBUTION,
            "rate": MetricType.RATE,
        }
    except ImportError:
        from ddtrace.internal.telemetry.metrics import CountMetric
        from ddtrace.internal.telemetry.metrics import DistributionMetric
        from ddtrace.internal.telemetry.metrics import GaugeMetric
        from ddtrace.internal.telemetry.metrics import RateMetric

        _OLD_TYPES = {
            "count": CountMetric,
            "gauge": GaugeMetric,
            "distribution": DistributionMetric,
            "rate": RateMetric,
        }

    HAS_OLD_API = True
except ImportError:
    HAS_OLD_API = False

_NS = TELEMETRY_NAMESPACE.TRACERS
_TAGS = (("integration_name", "somevalue"),)

if not HAS_OLD_API:
    from ddtrace.internal.telemetry import telemetry_writer

    # Map metric type -> the writer method that records it.
    _NEW_ADDERS = {
        "count": telemetry_writer.add_count_metric,
        "gauge": telemetry_writer.add_gauge_metric,
        "distribution": telemetry_writer.add_distribution_metric,
        "rate": telemetry_writer.add_rate_metric,
    }


def _old_add(namespace, metric_type: str, name: str) -> None:
    namespace.add_metric(_OLD_TYPES[metric_type], _NS, name, 10, tags=_TAGS)


class TelemetryAddMetric(Scenario):
    """
    This scenario checks to see if there's an impact on sending metrics via instrumentation telemetry
    """

    metric_type: str
    num_metrics: int
    per_metric: int

    # Override `_pyperf` instead of `run` so we get better control over how we run/time the
    # scenario. This way we only time the actual metric recording (or flush), not the setup.
    def _pyperf(self, loops: int) -> float:
        if self.name.startswith("record-"):
            return self.run_record(loops)
        return self.run_add_metric(loops)

    def run_add_metric(self, loops: int) -> float:
        metric_names = [str(i) for i in range(self.num_metrics)]
        total = 0.0

        if HAS_OLD_API:
            for _ in range(loops):
                namespace = _OldNamespace()
                st = time.perf_counter()
                for m in metric_names:
                    for _ in range(self.per_metric):
                        _old_add(namespace, self.metric_type, m)
                total += time.perf_counter() - st
            return total

        # New API: record metrics via the writer (aggregated in the native worker).
        add = _NEW_ADDERS[self.metric_type]
        for _ in range(loops):
            st = time.perf_counter()
            for m in metric_names:
                for _ in range(self.per_metric):
                    add(_NS, m, 10, _TAGS)
            total += time.perf_counter() - st
        return total

    def run_record(self, loops: int) -> float:
        # Record ``num_metrics`` distinct metrics (a count/gauge/distribution/rate mix), once
        # each. This measures the per-metric RECORDING cost on both revisions — deliberately
        # NOT a flush: the native worker aggregates and flushes off-thread, so there is no
        # cheap, I/O-free Python-side flush to benchmark on the candidate. Timing the same
        # "record N metrics" operation on both sides keeps the candidate-vs-baseline
        # comparison meaningful.
        pool = (
            [("count-%d" % i, "count") for i in range(250)]
            + [("gauge-%d" % i, "gauge") for i in range(250)]
            + [("distribution-%d" % i, "distribution") for i in range(250)]
            + [("rate-%d" % i, "rate") for i in range(250)]
        )
        step = len(pool) // self.num_metrics
        subset = pool[0 : len(pool) : step]
        total = 0.0

        if HAS_OLD_API:
            for _ in range(loops):
                namespace = _OldNamespace()
                st = time.perf_counter()
                for name, mtype in subset:
                    _old_add(namespace, mtype, name)
                total += time.perf_counter() - st
            return total

        for _ in range(loops):
            st = time.perf_counter()
            for name, mtype in subset:
                _NEW_ADDERS[mtype](_NS, name, 10, _TAGS)
            total += time.perf_counter() - st
        return total
