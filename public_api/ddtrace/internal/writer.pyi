import abc
from ddtrace.vendor.dogstatsd import DogStatsd as DogStatsd
from typing import Any

log: Any
LOG_ERR_INTERVAL: int
DEFAULT_SMA_WINDOW: int

class TraceWriter(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def recreate(self) -> TraceWriter: ...
    @abc.abstractmethod
    def stop(self, timeout: Union[builtins.float, None]=...) -> None: ...
    @abc.abstractmethod
    def write(self, spans: Union[builtins.list[ddtrace.span.Span], None]=...) -> None: ...
