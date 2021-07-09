import Any
from typing import Optional

log: Any
SPAN_NAME: str

class TraceMiddleware:
    app: Any = ...
    use_signals: Any = ...
    def __init__(self, app: Any, tracer: Any, service: str = ..., use_signals: bool = ..., distributed_tracing: Optional[Any] = ...) -> None: ...
