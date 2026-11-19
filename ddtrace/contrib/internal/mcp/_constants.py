CLIENT_TOOL_CALL_OPERATION_NAME = "client_tool_call"
SERVER_REQUEST_OPERATION_NAME = "server_request"
# This operation is handled the same as server_request but has a different name for legacy reasons
SERVER_TOOL_CALL_OPERATION_NAME = "server_tool_call"
# mcp>=2 server requests are traced from a server middleware rather than RequestResponder
SERVER_MIDDLEWARE_OPERATION_NAME = "server_middleware"
DD_TRACE_CONTEXT_META_KEY = "_dd_trace_context"
TELEMETRY_KEY = "telemetry"
COMPONENT = "mcp"
SESSION_OPERATION_NAME = "session"
# Dispatched with (response,) before a server returns a tools list, when DD_MCP_CAPTURE_INTENT is enabled, so
# LLMObs can add its intent argument to each tool's input schema.
TOOLS_LIST_RESPONSE_EVENT = "mcp.tools_list_response"
# Dispatched with (span, request) before a server tool call runs, so LLMObs can record and remove the intent
# argument. The request's arguments are modified in place.
TELEMETRY_ARGUMENT_EVENT = "mcp.telemetry_argument"
# Dispatched with (span, server_info) when a stateless mcp>=2 client session adopts its server's identity.
CLIENT_SESSION_SERVER_INFO_EVENT = "mcp.client_session.server_info"
