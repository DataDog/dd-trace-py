# OpenTelemetry HTTP semantic convention names, emitted in place of the Datadog names in
# ddtrace.ext.http. http.endpoint has no OTel equivalent and is kept in both modes because
# endpoint aggregation and ASM depend on it.
REQUEST_METHOD = "http.request.method"
REQUEST_METHOD_ORIGINAL = "http.request.method_original"
RESPONSE_STATUS_CODE = "http.response.status_code"
ROUTE = "http.route"
URL_FULL = "url.full"
URL_PATH = "url.path"
URL_QUERY = "url.query"
URL_SCHEME = "url.scheme"
USER_AGENT_ORIGINAL = "user_agent.original"
CLIENT_ADDRESS = "client.address"
SERVER_PORT = "server.port"
# OpenTelemetry equivalent of network.client.ip.
NETWORK_PEER_ADDRESS = "network.peer.address"
