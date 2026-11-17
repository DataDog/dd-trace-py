from typing import Any
from typing import Optional

from ddtrace.contrib.internal.mcp._constants import CLIENT_SESSION_SERVER_INFO_EVENT
from ddtrace.contrib.internal.mcp._constants import COMPONENT
from ddtrace.contrib.internal.mcp._constants import TELEMETRY_ARGUMENT_EVENT
from ddtrace.contrib.internal.mcp._constants import TOOLS_LIST_RESPONSE_EVENT
from ddtrace.internal import core
from ddtrace.llmobs._contrib._subscribers import get_integration
from ddtrace.llmobs._contrib._subscribers import register
from ddtrace.llmobs._contrib._subscribers import unregister
from ddtrace.llmobs._integrations.mcp import MCPIntegration


def _integration() -> Optional[MCPIntegration]:
    integration = get_integration(COMPONENT)
    return integration if isinstance(integration, MCPIntegration) else None


def _on_tools_list_response(response: Any) -> None:
    integration = _integration()
    if integration is not None:
        integration.inject_tools_list_response(response)


def _on_telemetry_argument(span: Any, request: Any) -> None:
    integration = _integration()
    if integration is not None:
        integration.process_telemetry_argument(span, request)


def _on_client_session_server_info(span: Any, server_info: Any) -> None:
    integration = _integration()
    if integration is not None:
        integration.set_client_session_server_info(span, server_info)


_LISTENERS = (
    (TOOLS_LIST_RESPONSE_EVENT, _on_tools_list_response),
    (TELEMETRY_ARGUMENT_EVENT, _on_telemetry_argument),
    (CLIENT_SESSION_SERVER_INFO_EVENT, _on_client_session_server_info),
)


def listen() -> None:
    register(COMPONENT, MCPIntegration)
    for event_name, listener in _LISTENERS:
        core.on(event_name, listener)


def unlisten() -> None:
    unregister(COMPONENT)
    for event_name, listener in _LISTENERS:
        core.reset_listeners(event_name, listener)
