import abc
from ddtrace import Span
from ddtrace.internal.processor.trace import TraceProcessor
from typing import Any, List, Optional

class TraceFilter(TraceProcessor, metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def process_trace(self, trace: builtins.list[ddtrace.span.Span]) -> Optional[List[Span]]: ...

class FilterRequestsOnUrl(TraceFilter):
    def __init__(self, regexps: Any) -> None: ...
    def process_trace(self, trace: builtins.list[ddtrace.span.Span]) -> Optional[List[Span]]: ...
