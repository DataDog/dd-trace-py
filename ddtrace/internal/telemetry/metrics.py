import abc
import time
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import six
from typing_extensions import Literal

from ..hostname import get_hostname


MetricType = Literal["count", "gauge", "rate"]


class Metric(six.with_metaclass(abc.ABCMeta)):
    """
    stores metrics which will be sent to the Telemetry Intake metrics to the Datadog Instrumentation Telemetry Org
    """

    HOST_NAME = get_hostname()

    def __init__(self, namespace, metric, metric_type, common, interval=None):
        # type: (str, str, MetricType, bool, Optional[int]) -> None
        """
        metric: metric name
        metric_type: type of metric (count/gauge/rate)
        common: set to True if a metric is common to all tracers, false if it is python specific
        interval: field set for gauge and rate metrics, any field set is ignored for count metrics (in secs)
        """
        self.metric = metric
        self.type = metric_type
        self.common = common
        self.interval = interval
        self._roll_up_interval = interval
        self.namespace = namespace
        self._points = []  # type: List[Tuple[int, int]]
        self._tags = {}  # type: Dict[str, str]
        self._count = 0

    @abc.abstractmethod
    def add_point(self, value=None):
        # type: (int) -> None
        """adds timestamped data point associated with a metric"""
        pass

    def set_tags(self, tags):
        # type: (Dict) -> None
        """sets a metrics tag"""
        self._tags = tags

    def set_tag(self, name, value):
        # type: (str, str) -> None
        """sets a metrics tag"""
        self._tags[name] = value

    def to_dict(self):
        # type: () -> Dict
        """returns a dictionary containing the metrics fields expected by the telemetry intake service"""
        return {
            "host": self.HOST_NAME,
            "metric": self.metric,
            "type": self.type,
            "common": self.common,
            "interval": self.interval,
            "points": self._points,
            "tags": self._tags,
        }


class CountMetric(Metric):
    def add_point(self, value=None):
        # type: (int) -> None
        """adds timestamped data point associated with a metric"""
        timestamp = int(time.time())
        self._count += 1.0
        self._points = [(timestamp, self._count)]


class GaugeMetric(Metric):
    def add_point(self, value=None):
        # type: (int) -> None
        """adds timestamped data point associated with a metric"""
        timestamp = int(time.time())
        self._points = [(timestamp, value)]


class RateMetric(Metric):
    def add_point(self, value=None):
        # type: (int) -> None
        """adds timestamped data point associated with a metric
        https://github.com/DataDog/datadogpy/blob/ee5ac16744407dcbd7a3640ee7b4456536460065/datadog/threadstats/metrics.py#L181
        """
        timestamp = int(time.time())
        self._count += 1.0
        self._points = [(timestamp, self._count / self.interval)]
