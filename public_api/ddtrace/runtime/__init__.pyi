import ddtrace.internal.runtime.runtime_metrics
from typing import Optional

class RuntimeMetrics:
    @staticmethod
    def enable(tracer: Optional[ddtrace.Tracer]=..., dogstatsd_url: Optional[str]=..., flush_interval: Optional[float]=...) -> None: ...
    @staticmethod
    def disable() -> None: ...
