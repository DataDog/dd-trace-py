"""
Standard http tags.

For example:

span.set_tag(URL, '/user/home')
span.set_tag(STATUS_CODE, 404)
"""

# tags
URL = "http.url"
METHOD = "http.method"
STATUS_CODE = "http.status_code"
USER_AGENT = "http.useragent"
STATUS_MSG = "http.status_msg"
QUERY_STRING = "http.query.string"
RETRIES_REMAIN = "http.retries_remain"
VERSION = "http.version"
CLIENT_IP = "http.client_ip"
ROUTE = "http.route"
REFERRER_HOSTNAME = "http.referrer_hostname"
ENDPOINT = "http.endpoint"

# OpenTelemetry HTTP semantic convention names, emitted in place of the Datadog names above.
# ENDPOINT has no OTel equivalent and is kept in both modes because endpoint aggregation and
# ASM depend on it.
OTEL_REQUEST_METHOD = "http.request.method"
OTEL_REQUEST_METHOD_ORIGINAL = "http.request.method_original"
OTEL_RESPONSE_STATUS_CODE = "http.response.status_code"
OTEL_ROUTE = "http.route"
OTEL_URL_FULL = "url.full"
OTEL_URL_PATH = "url.path"
OTEL_URL_QUERY = "url.query"
OTEL_URL_SCHEME = "url.scheme"
OTEL_USER_AGENT_ORIGINAL = "user_agent.original"
OTEL_CLIENT_ADDRESS = "client.address"

# HTTP headers
REFERER_HEADER = "referer"

# template render span type
TEMPLATE = "template"
