# -*- coding: utf-8 -*-
import abc
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Text
from typing import Tuple

import six

from ddtrace.internal.telemetry.constants import TELEMETRY_METRIC_TYPE_COUNT
from ddtrace.internal.telemetry.constants import TELEMETRY_METRIC_TYPE_DISTRIBUTIONS
from ddtrace.internal.telemetry.constants import TELEMETRY_METRIC_TYPE_GAUGE
from ddtrace.internal.telemetry.constants import TELEMETRY_METRIC_TYPE_RATE


MetricType = Text
MetricTagType = Dict[str, Any]


class Metric(six.with_metaclass(abc.ABCMeta)):
    """
    Telemetry Metrics are stored in DD dashboards, check the metrics in datadoghq.com/metric/explorer
    """

    metric_type = ""
    _points = []  # type: List[Tuple[float, float]]

    def __init__(self, namespace, name, tags, common, interval=None):
        # type: (str, str, MetricTagType, bool, Optional[float]) -> None
        """
        namespace: the scope of the metric: tracer, appsec, etc.
        name: string
        tags: extra information attached to a metric
        common: set to True if a metric is common to all tracers, false if it is python specific
        interval: field set for gauge and rate metrics, any field set is ignored for count metrics (in secs)
        """
        self.name = name
        self.is_common_to_all_tracers = common
        self.interval = interval
        self.namespace = namespace
        self._tags = tags  # type: MetricTagType
        self._count = 0.0
        self._points = []  # type: List[float]

    @property
    def id(self):
        """
        https://www.datadoghq.com/blog/the-power-of-tagged-metrics/#whats-a-metric-tag
        """
        return self.name + str(self._tags)

    def __hash__(self):
        return self.id

    @abc.abstractmethod
    def add_point(self, value=1.0):
        # type: (float) -> None
        """adds timestamped data point associated with a metric"""
        pass

    def set_tags(self, tags):
        # type: (MetricTagType) -> None
        """sets a metrics tag"""
        if tags:
            for k, v in iter(tags.items()):
                self.set_tag(k, v)

    def set_tag(self, name, value):
        # type: (str, str) -> None
        """sets a metrics tag"""
        self._tags[name] = value

    def to_dict(self):
        # type: () -> Dict
        """returns a dictionary containing the metrics fields expected by the telemetry intake service"""
        data = {
            "metric": self.name,
            "type": self.metric_type,
            "common": self.is_common_to_all_tracers,
            "points": self._points,
            "tags": ["%s:%s" % (k, v) for k, v in self._tags.items()],
        }
        if self.interval is not None:
            data["interval"] = int(self.interval)
        return data


class CountMetric(Metric):
    """
    A count type adds up all the submitted values in a time interval. This would be suitable for a
    metric tracking the number of website hits, for instance.
    """

    metric_type = TELEMETRY_METRIC_TYPE_COUNT

    def __init__(self, namespace, name, tags, common, interval=None):
        super(CountMetric, self).__init__(namespace, name, tags, common, interval)
        self.interval = None

    def add_point(self, value=1.0):
        # type: (float) -> None
        """adds timestamped data point associated with a metric"""
        timestamp = time.time()
        if len(self._points) == 0:
            self._points = [[timestamp, float(value)]]  # type: ignore
        else:
            self._points[0][1] += float(value)  # type: ignore


class GaugeMetric(Metric):
    """
    A gauge type takes the last value reported during the interval. This type would make sense for tracking RAM or
    CPU usage, where taking the last value provides a representative picture of the host’s behavior during the time
    interval. In this case, using a different type such as count would probably lead to inaccurate and extreme values.
    Choosing the correct metric type ensures accurate data.
    """

    metric_type = TELEMETRY_METRIC_TYPE_GAUGE

    def add_point(self, value=1.0):
        # type: (float) -> None
        """adds timestamped data point associated with a metric"""
        timestamp = time.time()
        self._points = [(timestamp, float(value))]


class RateMetric(Metric):
    """
    The rate type takes the count and divides it by the length of the time interval. This is useful if you’re
    interested in the number of hits per second.
    """

    metric_type = TELEMETRY_METRIC_TYPE_RATE

    def add_point(self, value=1.0):
        # type: (float) -> None
        """Example:
        https://github.com/DataDog/datadogpy/blob/ee5ac16744407dcbd7a3640ee7b4456536460065/datadog/threadstats/metrics.py#L181
        """
        timestamp = time.time()
        self._count += value
        rate = (self._count / float(self.interval)) if self.interval else 0.0
        self._points = [(timestamp, rate)]


class DistributionMetric(Metric):
    """
    The rate type takes the count and divides it by the length of the time interval. This is useful if you’re
    interested in the number of hits per second.
    """

    metric_type = TELEMETRY_METRIC_TYPE_DISTRIBUTIONS

    def __init__(self, namespace, name, tags, common, interval=None):
        super(DistributionMetric, self).__init__(namespace, name, tags, common, interval)
        self.interval = None

    def add_point(self, value=1.0):
        # type: (float) -> None
        """Example:
        https://github.com/DataDog/datadogpy/blob/ee5ac16744407dcbd7a3640ee7b4456536460065/datadog/threadstats/metrics.py#L181
        """
        self._points.append(float(value))  # type: ignore

    def to_dict(self):
        # type: () -> Dict
        """returns a dictionary containing the metrics fields expected by the telemetry intake service"""
        data = {
            "metric": self.name,
            "points": self._points,
            "tags": ["%s:%s" % (k, v) for k, v in self._tags.items()],
        }
        return data
