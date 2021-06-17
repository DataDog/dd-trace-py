import Any
from ddtrace.span import Span
from typing import Set, Tuple

EXCLUDED_ENDPOINT: Any
EXCLUDED_ENDPOINT_TAGS: Any

def truncate_arg_value(value: Any, max_len: int=...) -> Any: ...
def add_span_arg_tags(span: Span, endpoint_name: str, args: Tuple[Any], args_names: Tuple[str], args_traced: Set[str]) -> None: ...

REGION: str
AGENT: str
OPERATION: str
