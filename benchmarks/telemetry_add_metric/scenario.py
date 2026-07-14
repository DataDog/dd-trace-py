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
        if self.name.startswith("flush-"):
            return self.run_flush(loops)
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

    def run_flush(self, loops: int) -> float:
        if HAS_OLD_API:
            return self._run_flush_old(loops)
        return self._run_flush_new(loops)

    def _run_flush_old(self, loops: int) -> float:
        # Pool of metrics to use for adding
        pool = (
            [("count-%d" % i, "count") for i in range(250)]
            + [("gauge-%d" % i, "gauge") for i in range(250)]
            + [("distribution-%d" % i, "distribution") for i in range(250)]
            + [("rate-%d" % i, "rate") for i in range(250)]
        )
        step = len(pool) // self.num_metrics
        total = 0.0

        # Pre-fill a dummy namespace with metrics
        dummy_namespace = _OldNamespace()
        for i in range(0, len(pool), step):
            name, mtype = pool[i]
            _old_add(dummy_namespace, mtype, name)

        for _ in range(loops):
            namespace = _OldNamespace()
            # Copy the dummy metrics to the new namespace, this saves time on adding metrics
            namespace._metrics_data = dummy_namespace._metrics_data.copy()
            st = time.perf_counter()
            namespace.flush()
            total += time.perf_counter() - st
        return total

    def _run_flush_new(self, loops: int) -> float:
        # On this branch metric aggregation and flushing happen in the native worker,
        # off the Python hot path — there is no cheap Python-side "serialize" step to
        # isolate, and forcing a real flush would block on the endpoint I/O. So measure
        # the Python-side cost of recording a heartbeat's worth of metrics instead.
        pool = (
            [("count-%d" % i, telemetry_writer.add_count_metric) for i in range(250)]
            + [("gauge-%d" % i, telemetry_writer.add_gauge_metric) for i in range(250)]
            + [("distribution-%d" % i, telemetry_writer.add_distribution_metric) for i in range(250)]
            + [("rate-%d" % i, telemetry_writer.add_rate_metric) for i in range(250)]
        )
        step = len(pool) // self.num_metrics
        subset = pool[0 : len(pool) : step]
        total = 0.0
        for _ in range(loops):
            st = time.perf_counter()
            for name, add in subset:
                add(_NS, name, 10, _TAGS)
            total += time.perf_counter() - st
        return total
