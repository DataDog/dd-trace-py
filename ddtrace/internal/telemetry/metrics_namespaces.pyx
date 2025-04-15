# cython: freethreading_compatible=True
import enum
import time
from typing import Optional
from typing import Tuple

from ddtrace.internal import forksafe
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_DISTRIBUTION
from ddtrace.internal.telemetry.constants import TELEMETRY_TYPE_GENERATE_METRICS


MetricTagType = Optional[Tuple[Tuple[str, str], ...]]


class MetricType(str, enum.Enum):
    DISTRIBUTION = "distributions"
    COUNT = "count"
    GAUGE = "gauge"
    RATE = "rate"


cdef class MetricNamespace:
    cdef object _metrics_data_lock
    cdef public dict _metrics_data

    def __cinit__(self):
        self._metrics_data_lock = forksafe.Lock()
        self._metrics_data = {}

    def flush(self, interval: float = None):
        cdef float _interval = float(interval or 1.0)
        cdef int now
        cdef dict data
        cdef tuple _tags
        cdef list tags
        cdef str name
        cdef str namespace
        cdef object metric_type
        cdef tuple metric_id
        cdef object value
        cdef dict namespace_metrics

        with self._metrics_data_lock:
            namespace_metrics, self._metrics_data = self._metrics_data, {}

        now = int(time.time())
        data = {
            TELEMETRY_TYPE_GENERATE_METRICS: {},
            TELEMETRY_TYPE_DISTRIBUTION: {},
        }
        for metric_id, value in namespace_metrics.items():
            name, namespace, _tags, metric_type = metric_id
            tags = ["{}:{}".format(k, v).lower() for k, v in _tags] if _tags else []
            if metric_type is MetricType.DISTRIBUTION:
                data[TELEMETRY_TYPE_DISTRIBUTION].setdefault(namespace, []).append({
                    "metric": name,
                    "points": value,
                    "tags": tags,
                })
            else:
                if metric_type is MetricType.RATE:
                    value = value / _interval
                metric = {
                    "metric": name,
                    "type": metric_type.value,
                    "common": True,
                    "points": [[now, value]],
                    "tags": tags,
                }
                if metric_type in (MetricType.RATE, MetricType.GAUGE):
                    metric["interval"] = _interval
                data[TELEMETRY_TYPE_GENERATE_METRICS].setdefault(namespace, []).append(metric)

        return data

    def add_metric(
        self,
        metric_type: MetricType,
        namespace: TELEMETRY_NAMESPACE,
        name: str,
        value: float = 1.0,
        tags: object = None,
    ) -> None:
        """
        Adds a new telemetry metric to the internal metrics.
        Telemetry metrics are stored under "dd.instrumentation_telemetry_data.<namespace>.<name>".
        """
        cdef float v
        cdef tuple metric_id
        metric_id = (name, namespace.value, tags, metric_type)
        if metric_type is MetricType.DISTRIBUTION:
            with self._metrics_data_lock:
                self._metrics_data.setdefault(metric_id, []).append(value)
        elif metric_type is MetricType.GAUGE:
            # Dict writes are atomic, no need to lock
            self._metrics_data[metric_id] = value
        else:
            with self._metrics_data_lock:
                v = self._metrics_data.get(metric_id, 0)
                self._metrics_data[metric_id] = v + value
