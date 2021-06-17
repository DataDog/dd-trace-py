from ..context import Context
from typing import Any, Dict

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
    def inject(span_context: Context, headers: Dict[str, str]) -> None: ...
    @staticmethod
    def extract(headers: Dict[str, str]) -> Context: ...
