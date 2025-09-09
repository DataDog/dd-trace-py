import typing

from ddtrace.trace import Tracer
from ddtrace.profiling import collector

class StackCollector(collector.PeriodicCollector):
    tracer: typing.Optional[Tracer]
