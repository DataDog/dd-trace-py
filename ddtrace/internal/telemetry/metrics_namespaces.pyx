# cython: freethreading_compatible=True
import enum
import time
from typing import Optional
from typing import Tuple

from ddtrace.internal import forksafe
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.constants import TELEMETRY_EVENT_TYPE


MetricTagType = Optional[Tuple[Tuple[str, str], ...]]


class MetricType(str, enum.Enum):
    DISTRIBUTION = "distributions"
    COUNT = "count"
    GAUGE = "gauge"
    RATE = "rate"


cdef class MetricNamespace:
    cdef object _metrics_data_lock
    cdef public dict _metrics_data
    # Cache enum objects at class level for maximum performance
    cdef readonly object _metrics_key
    cdef readonly object _distributions_key

    def __cinit__(self):
        self._metrics_data_lock = forksafe.Lock()
        self._metrics_data = {}
        # Initialize cached enum references
        self._metrics_key = TELEMETRY_EVENT_TYPE.METRICS
        self._distributions_key = TELEMETRY_EVENT_TYPE.DISTRIBUTIONS

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
            self._metrics_key: {},
            self._distributions_key: {},
        }
        for metric_id, value in namespace_metrics.items():
            name, namespace, _tags, metric_type = metric_id
            tags = [f"{k}:{v}".lower() for k, v in _tags] if _tags else []
            if metric_type is MetricType.DISTRIBUTION:
                payload_type = self._distributions_key
                metric = {
                    "metric": name,
                    "points": value,
                    "tags": tags,
                }
            else:
                payload_type = self._metrics_key
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

            namespace_dict = data[payload_type]
            if namespace not in namespace_dict:
                namespace_dict[namespace] = [metric]
            else:
                namespace_dict[namespace].append(metric)
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
        metric_id = (name, namespace.value, tags, metric_type)
        if metric_type is MetricType.COUNT or metric_type is MetricType.RATE:
            with self._metrics_data_lock:
                existing = self._metrics_data.get(metric_id)
                if existing is not None:
                    self._metrics_data[metric_id] = existing + value
                else:
                    self._metrics_data[metric_id] = value
        elif metric_type is MetricType.GAUGE:
            self._metrics_data[metric_id] = value
        else:  # MetricType.DISTRIBUTION
            with self._metrics_data_lock:
                existing = self._metrics_data.get(metric_id)
                if existing is not None:
                    existing.append(value)
                else:
                    self._metrics_data[metric_id] = [value]
