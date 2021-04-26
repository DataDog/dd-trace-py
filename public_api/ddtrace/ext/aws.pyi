import Any

EXCLUDED_ENDPOINT: Any
EXCLUDED_ENDPOINT_TAGS: Any

def truncate_arg_value(value: Any, max_len: builtins.int=...) -> Any: ...
def add_span_arg_tags(span: ddtrace.span.Span, endpoint_name: builtins.str, args: Tuple[Any], args_names: Tuple[builtins.str], args_traced: builtins.set[builtins.str]) -> None: ...

REGION: str
AGENT: str
OPERATION: str
