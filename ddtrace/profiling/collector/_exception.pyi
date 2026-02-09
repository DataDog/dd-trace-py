from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.profiling import collector


class ExceptionCollector(collector.Collector):
    collect_message: bool

    def __init__(
        self,
        max_nframe: Optional[int] = None,
        sampling_interval: Optional[int] = None,
        collect_message: Optional[bool] = None,
    ) -> None: ...
    def _start_service(self) -> None: ...
    def _stop_service(self) -> None: ...
    def snapshot(self) -> None: ...
    def get_stats(self) -> Dict[str, int]: ...


def _on_exception_handled(code: Any, instruction_offset: int, exception: BaseException) -> None: ...
def _on_exception_bytecode(arg: Any) -> None: ...