from typing import Any

log: Any
REQUEST: str
RESPONSE: str
NORMALIZE_PATTERN: Any

def store_request_headers(headers: Any, span: Any, integration_config: Any) -> None: ...
def store_response_headers(headers: Any, span: Any, integration_config: Any) -> None: ...
