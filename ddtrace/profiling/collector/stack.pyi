import typing

from ddtrace.trace import Tracer

class StackCollector:
    tracer: typing.Optional[Tracer]
