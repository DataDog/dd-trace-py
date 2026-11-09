CLIENT_TOOL_CALL_OPERATION_NAME = "client_tool_call"
SERVER_REQUEST_OPERATION_NAME = "server_request"
# This operation is handled the same as server_request but has a different name for legacy reasons
SERVER_TOOL_CALL_OPERATION_NAME = "server_tool_call"
# mcp>=2 server requests are traced from a server middleware rather than RequestResponder
SERVER_MIDDLEWARE_OPERATION_NAME = "server_middleware"
DD_TRACE_CONTEXT_META_KEY = "_dd_trace_context"
TELEMETRY_KEY = "telemetry"
