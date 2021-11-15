import time
from typing import Dict
from typing import List
from typing import Optional

from ...hostname import get_hostname


class Series:
    GAUGE = "gauge"
    COUNT = "count"
    RATE = "rate"

    COMMON_TELEMETRY_METRIC = "dd.app_telemetry.tracers.%s"
    PYTHON_TELEMETRY_METRIC = "dd.app_telemetry.tracers.python.%s"

    def __init__(self, name, metric_type=COUNT, is_common=False, interval=None):
        # type: (str, str, bool, Optional[int]) -> None
        self.points = []  # type: List[List[int]]
        self.tags = {}  # type: Dict[str, str]
        self.type = metric_type  # type: str
        self.interval = interval  # type: Optional[int]
        self.host = get_hostname()  # type: str
        self.metric = Series.set_metric_name(name, is_common)  # type: str

    @classmethod
    def set_metric_name(cls, metric_name, is_common=False):
        # type: (str, bool) -> str
        if "dd.app_telemetry.tracers." in metric_name:
            return metric_name
        elif is_common:
            return cls.COMMON_TELEMETRY_METRIC % metric_name

        return cls.PYTHON_TELEMETRY_METRIC % metric_name

    def add_point(self, value):
        # type: (int) -> None
        timestamp = int(time.time())  # type: int
        self.points.append([timestamp, value])

    def add_tag(self, name, value):
        # type: (str, str) -> None
        self.tags[name] = value

    def combine(self, other_series):
        # type: (Series) -> None
        if self.type != other_series.type:
            raise Exception("Metrics with the same name should have the same type")

        if self.interval != other_series.interval:
            raise Exception("Metrics with the same name should have the same interval")

        self.tags.update(other_series.tags)
        self.points.extend(other_series.points)

    def to_dict(self):
        # type: () -> Dict
        return {
            "metric": self.metric,
            "points": self.points,
            "tags": self.tags,
            "type": self.type,
            "interval": self.interval,
            "host": self.host,
        }
