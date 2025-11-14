import typing

from ddtrace.trace import Tracer
from ddtrace.profiling import collector

class StackCollector(collector.PeriodicCollector):
    tracer: typing.Optional[Tracer]
    min_interval_time: float

    def _init(self) -> None: ...
    def _compute_new_interval(self, used_wall_time_ns: int) -> float: ...
