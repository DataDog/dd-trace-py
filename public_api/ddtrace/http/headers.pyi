from typing import Any

log: Any
REQUEST: str
RESPONSE: str
NORMALIZE_PATTERN: Any

def store_request_headers(headers: builtins.dict[builtins.str, builtins.str], span: ddtrace.span.Span, integration_config: ddtrace.settings.integration.IntegrationConfig) -> None: ...
def store_response_headers(headers: builtins.dict[builtins.str, builtins.str], span: ddtrace.span.Span, integration_config: ddtrace.settings.integration.IntegrationConfig) -> None: ...
