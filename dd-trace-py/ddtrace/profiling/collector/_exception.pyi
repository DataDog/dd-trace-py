from types import CodeType
from typing import Optional

from ddtrace.profiling import collector

HAS_MONITORING: bool
MAX_EXCEPTION_MESSAGE_LEN: int

class ExceptionCollector(collector.Collector):
    _sampling_interval: int
    _collect_message: bool
    _monitoring_registered: bool
    def __init__(
        self,
        sampling_interval: Optional[int] = None,
        collect_message: Optional[bool] = None,
    ) -> None: ...
    def _start_service(self) -> None: ...
    def _stop_service(self) -> None: ...

def _on_exception_handled(code: CodeType, instruction_offset: int, exception: BaseException) -> None: ...
