import abc
from ddtrace import Span
from ddtrace.vendor.dogstatsd import DogStatsd as DogStatsd
from typing import Any, List, Optional

log: Any
LOG_ERR_INTERVAL: int
DEFAULT_SMA_WINDOW: int
DEFAULT_BUFFER_SIZE: Any
DEFAULT_MAX_PAYLOAD_SIZE: Any
DEFAULT_PROCESSING_INTERVAL: float

class TraceWriter(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def recreate(self) -> TraceWriter: ...
    @abc.abstractmethod
    def stop(self, timeout: Optional[float]=...) -> None: ...
    @abc.abstractmethod
    def write(self, spans: Optional[List[Span]]=...) -> None: ...
