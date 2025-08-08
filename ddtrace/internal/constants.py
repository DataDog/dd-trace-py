from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT


PROPAGATION_STYLE_DATADOG = "datadog"
PROPAGATION_STYLE_B3_MULTI = "b3multi"
PROPAGATION_STYLE_B3_SINGLE = "b3"
_PROPAGATION_STYLE_W3C_TRACECONTEXT = "tracecontext"
_PROPAGATION_STYLE_NONE = "none"
_PROPAGATION_STYLE_DEFAULT = "datadog,tracecontext,baggage"
_PROPAGATION_STYLE_BAGGAGE = "baggage"
PROPAGATION_STYLE_ALL = (
    _PROPAGATION_STYLE_W3C_TRACECONTEXT,
    PROPAGATION_STYLE_DATADOG,
    PROPAGATION_STYLE_B3_MULTI,
    PROPAGATION_STYLE_B3_SINGLE,
    _PROPAGATION_STYLE_NONE,
    _PROPAGATION_STYLE_BAGGAGE,
)
_PROPAGATION_BEHAVIOR_CONTINUE = "continue"
_PROPAGATION_BEHAVIOR_IGNORE = "ignore"
_PROPAGATION_BEHAVIOR_RESTART = "restart"
_PROPAGATION_BEHAVIOR_DEFAULT = _PROPAGATION_BEHAVIOR_CONTINUE
W3C_TRACESTATE_KEY = "tracestate"
W3C_TRACEPARENT_KEY = "traceparent"
W3C_TRACESTATE_PARENT_ID_KEY = "p"
W3C_TRACESTATE_ORIGIN_KEY = "o"
W3C_TRACESTATE_SAMPLING_PRIORITY_KEY = "s"
DEFAULT_SAMPLING_RATE_LIMIT = 100
SAMPLING_HASH_MODULO = 1 << 64
# Big prime number to make hashing better distributed, it has to be the same factor as the Agent
# and other tracers to allow chained sampling
SAMPLING_KNUTH_FACTOR = 1111111111111111111
SAMPLING_DECISION_TRACE_TAG_KEY = "_dd.p.dm"
LAST_DD_PARENT_ID_KEY = "_dd.parent_id"
DEFAULT_SERVICE_NAME = "unnamed-python-service"
# Used to set the name of an integration on a span
COMPONENT = "component"
HIGHER_ORDER_TRACE_ID_BITS = "_dd.p.tid"
MAX_UINT_64BITS = (1 << 64) - 1
MIN_INT_64BITS = -(2**63)
MAX_INT_64BITS = 2**63 - 1
SAMPLING_DECISION_MAKER_INHERITED = "_dd.dm.inherited"
SAMPLING_DECISION_MAKER_SERVICE = "_dd.dm.service"
SAMPLING_DECISION_MAKER_RESOURCE = "_dd.dm.resource"
SPAN_LINK_KIND = "dd.kind"
SPAN_LINKS_KEY = "_dd.span_links"
SPAN_EVENTS_KEY = "events"
SPAN_API_DATADOG = "datadog"
SPAN_API_OTEL = "otel"
SPAN_API_OPENTRACING = "opentracing"
DEFAULT_BUFFER_SIZE = 20 << 20  # 20 MB
DEFAULT_MAX_PAYLOAD_SIZE = 20 << 20  # 20 MB
DEFAULT_PROCESSING_INTERVAL = 1.0
DEFAULT_REUSE_CONNECTIONS = False
BLOCKED_RESPONSE_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>You've been blocked</title><style>a,body,div,html,span{margin:0;padding:0;border:0;font-size:100%;font:inherit;vertical-align:baseline}body{background:-webkit-radial-gradient(26% 19%,circle,#fff,#f4f7f9);background:radial-gradient(circle at 26% 19%,#fff,#f4f7f9);display:-webkit-box;display:-ms-flexbox;display:flex;-webkit-box-pack:center;-ms-flex-pack:center;justify-content:center;-webkit-box-align:center;-ms-flex-align:center;align-items:center;-ms-flex-line-pack:center;align-content:center;width:100%;min-height:100vh;line-height:1;flex-direction:column}p{display:block}main{text-align:center;flex:1;display:-webkit-box;display:-ms-flexbox;display:flex;-webkit-box-pack:center;-ms-flex-pack:center;justify-content:center;-webkit-box-align:center;-ms-flex-align:center;align-items:center;-ms-flex-line-pack:center;align-content:center;flex-direction:column}p{font-size:18px;line-height:normal;color:#646464;font-family:sans-serif;font-weight:400}a{color:#4842b7}footer{width:100%;text-align:center}footer p{font-size:16px}</style></head><body><main><p>Sorry, you cannot access this page. Please contact the customer service team.</p></main><footer><p>Security provided by <a href="https://www.datadoghq.com/product/security-platform/application-security-monitoring/" target="_blank">Datadog</a></p></footer></body></html>"""  # noqa: E501
BLOCKED_RESPONSE_JSON = '{"errors":[{"title":"You\'ve been blocked","detail":"Sorry, you cannot access this page. Please contact the customer service team. Security provided by Datadog."}]}'  # noqa: E501
HTTP_REQUEST_BLOCKED = "http.request.blocked"
RESPONSE_HEADERS = "http.response.headers"
HTTP_REQUEST_QUERY = "http.request.query"
HTTP_REQUEST_COOKIE_VALUE = "http.request.cookie.value"
HTTP_REQUEST_COOKIE_NAME = "http.request.cookie.name"
HTTP_REQUEST_PATH = "http.request.path"
HTTP_REQUEST_HEADER_NAME = "http.request.header.name"
HTTP_REQUEST_HEADER = "http.request.header"
HTTP_REQUEST_PARAMETER = "http.request.parameter"
HTTP_REQUEST_BODY = "http.request.body"
HTTP_REQUEST_PATH_PARAMETER = "http.request.path.parameter"
REQUEST_PATH_PARAMS = "http.request.path_params"
STATUS_403_TYPE_AUTO = {"status_code": 403, "type": "auto"}

CONTAINER_ID_HEADER_NAME = "Datadog-Container-Id"

ENTITY_ID_HEADER_NAME = "Datadog-Entity-ID"

EXTERNAL_ENV_HEADER_NAME = "Datadog-External-Env"
EXTERNAL_ENV_ENVIRONMENT_VARIABLE = "DD_EXTERNAL_ENV"

MESSAGING_DESTINATION_NAME = "messaging.destination.name"
MESSAGING_MESSAGE_ID = "messaging.message_id"
MESSAGING_OPERATION = "messaging.operation"
MESSAGING_SYSTEM = "messaging.system"

NETWORK_DESTINATION_NAME = "network.destination.name"

FLASK_ENDPOINT = "flask.endpoint"
FLASK_VIEW_ARGS = "flask.view_args"
FLASK_URL_RULE = "flask.url_rule"

_HTTPLIB_NO_TRACE_REQUEST = "_dd_no_trace"
DEFAULT_TIMEOUT = 2.0

# baggage
DD_TRACE_BAGGAGE_MAX_ITEMS = 64
DD_TRACE_BAGGAGE_MAX_BYTES = 8192
BAGGAGE_TAG_PREFIX = "baggage."

SPAN_EVENTS_HAS_EXCEPTION = "_dd.span_events.has_exception"
COLLECTOR_MAX_SIZE_PER_SPAN = 100

LOG_ATTR_TRACE_ID = "dd.trace_id"
LOG_ATTR_SPAN_ID = "dd.span_id"
LOG_ATTR_ENV = "dd.env"
LOG_ATTR_VERSION = "dd.version"
LOG_ATTR_SERVICE = "dd.service"
LOG_ATTR_VALUE_ZERO = "0"
LOG_ATTR_VALUE_EMPTY = ""


class SamplingMechanism(object):
    DEFAULT = 0
    AGENT_RATE_BY_SERVICE = 1
    REMOTE_RATE = 2  # not used, this mechanism is deprecated
    LOCAL_USER_TRACE_SAMPLING_RULE = 3
    MANUAL = 4
    APPSEC = 5
    REMOTE_RATE_USER = 6  # not used, this mechanism is deprecated
    REMOTE_RATE_DATADOG = 7  # not used, this mechanism is deprecated
    SPAN_SAMPLING_RULE = 8
    OTLP_INGEST_PROBABILISTIC_SAMPLING = 9  # not used in ddtrace
    DATA_JOBS_MONITORING = 10  # not used in ddtrace
    REMOTE_USER_TRACE_SAMPLING_RULE = 11
    REMOTE_DYNAMIC_TRACE_SAMPLING_RULE = 12


SAMPLING_MECHANISM_TO_PRIORITIES = {
    # TODO(munir): Update mapping to include single span sampling and appsec sampling mechanisms
    SamplingMechanism.AGENT_RATE_BY_SERVICE: (AUTO_KEEP, AUTO_REJECT),
    SamplingMechanism.DEFAULT: (AUTO_KEEP, AUTO_REJECT),
    SamplingMechanism.MANUAL: (USER_KEEP, USER_REJECT),
    SamplingMechanism.APPSEC: (AUTO_KEEP, AUTO_REJECT),
    SamplingMechanism.LOCAL_USER_TRACE_SAMPLING_RULE: (USER_KEEP, USER_REJECT),
    SamplingMechanism.REMOTE_USER_TRACE_SAMPLING_RULE: (USER_KEEP, USER_REJECT),
    SamplingMechanism.REMOTE_DYNAMIC_TRACE_SAMPLING_RULE: (USER_KEEP, USER_REJECT),
}
_KEEP_PRIORITY_INDEX = 0
_REJECT_PRIORITY_INDEX = 1


# List of support values in DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED
class EXPERIMENTAL_FEATURES:
    # Enables submitting runtime metrics as gauges (instead of distributions)
    RUNTIME_METRICS = "DD_RUNTIME_METRICS_ENABLED"


AI_GUARD_ENABLED = "DD_AI_GUARD_ENABLED"
AI_GUARD_ENDPOINT = "DD_AI_GUARD_ENDPOINT"
DD_APPLICATION_KEY = "DD_APP_KEY"
