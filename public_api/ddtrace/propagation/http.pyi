import Any
from ..context import Context

log: Any
HTTP_HEADER_TRACE_ID: str
HTTP_HEADER_PARENT_ID: str
HTTP_HEADER_SAMPLING_PRIORITY: str
HTTP_HEADER_ORIGIN: str
POSSIBLE_HTTP_HEADER_TRACE_IDS: Any
POSSIBLE_HTTP_HEADER_PARENT_IDS: Any
POSSIBLE_HTTP_HEADER_SAMPLING_PRIORITIES: Any
POSSIBLE_HTTP_HEADER_ORIGIN: Any

class HTTPPropagator:
    @staticmethod
    def inject(span_context: Any, headers: Any) -> None: ...
    @staticmethod
    def extract(headers: builtins.dict[builtins.str, builtins.str]) -> Context: ...
