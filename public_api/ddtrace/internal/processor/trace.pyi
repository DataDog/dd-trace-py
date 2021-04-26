import abc
from ddtrace.span import Span
from typing import Any, List, Optional

log: Any

class TraceProcessor(metaclass=abc.ABCMeta):
    def __attrs_post_init__(self) -> None: ...
    @abc.abstractmethod
    def process_trace(self, trace: builtins.list[ddtrace.span.Span]) -> Optional[List[Span]]: ...
