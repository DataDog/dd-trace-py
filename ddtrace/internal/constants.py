from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT


DEFAULT_SAMPLING_RATE_LIMIT = 100
SAMPLING_HASH_MODULO = 1 << 64
# Big prime number to make hashing better distributed, it has to be the same factor as the Agent
# and other tracers to allow chained sampling
SAMPLING_KNUTH_FACTOR = 1111111111111111111
# Used to set the name of an integration on a span
COMPONENT = "component"
MAX_UINT_64BITS = (1 << 64) - 1
MIN_INT_64BITS = -(2**63)
MAX_INT_64BITS = 2**63 - 1
SPAN_EVENTS_KEY = "events"
SPAN_API_DATADOG = "datadog"
SPAN_API_OTEL = "otel"
SPAN_API_OPENTRACING = "opentracing"
DEFAULT_BUFFER_SIZE = 20 << 20  # 20 MB
DEFAULT_MAX_PAYLOAD_SIZE = 20 << 20  # 20 MB
DEFAULT_PROCESSING_INTERVAL = 1.0
DEFAULT_REUSE_CONNECTIONS = False
BLOCKED_RESPONSE_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>You've been blocked</title><style>a,body,div,html,span{margin:0;padding:0;border:0;font-size:100%;font:inherit;vertical-align:baseline}body{background:-webkit-radial-gradient(26% 19%,circle,#fff,#f4f7f9);background:radial-gradient(circle at 26% 19%,#fff,#f4f7f9);display:-webkit-box;display:-ms-flexbox;display:flex;-webkit-box-pack:center;-ms-flex-pack:center;justify-content:center;-webkit-box-align:center;-ms-flex-align:center;align-items:center;-ms-flex-line-pack:center;align-content:center;width:100%;min-height:100vh;line-height:1;flex-direction:column}p{display:block}main{text-align:center;flex:1;display:-webkit-box;display:-ms-flexbox;display:flex;-webkit-box-pack:center;-ms-flex-pack:center;justify-content:center;-webkit-box-align:center;-ms-flex-align:center;align-items:center;-ms-flex-line-pack:center;align-content:center;flex-direction:column}p{font-size:18px;line-height:normal;color:#646464;font-family:sans-serif;font-weight:400}a{color:#4842b7}footer{width:100%;text-align:center}footer p{font-size:16px}.security-response-id{font-size:14px;color:#999;margin-top:20px;font-family:monospace}</style></head><body><main><p>Sorry, you cannot access this page. Please contact the customer service team.</p><p class="security-response-id">Security Response ID: [security_response_id]</p></main><footer><p>Security provided by <a href="https://www.datadoghq.com/product/security-platform/application-security-monitoring/" target="_blank" rel="noopener noreferrer">Datadog</a></p></footer></body></html>"""  # noqa: E501
BLOCKED_RESPONSE_JSON = """{"errors":[{"title":"You've been blocked","detail":"Sorry, you cannot access this page. Please contact the customer service team. Security provided by Datadog."}],"security_response_id":"[security_response_id]"}"""  # noqa: E501
HTTP_REQUEST_BLOCKED = "http.request.blocked"
HTTP_REQUEST_UPGRADED = "http.upgraded"
RESPONSE_HEADERS = "http.response.headers"
REQUEST_PATH_PARAMS = "http.request.path_params"
STATUS_403_TYPE_AUTO = {"status_code": 403, "type": "auto"}

CONTAINER_ID_HEADER_NAME = "Datadog-Container-Id"
CONTAINER_TAGS_HASH = "Datadog-Container-Tags-Hash"

ENTITY_ID_HEADER_NAME = "Datadog-Entity-ID"

EXTERNAL_ENV_HEADER_NAME = "Datadog-External-Env"
EXTERNAL_ENV_ENVIRONMENT_VARIABLE = "DD_EXTERNAL_ENV"

USER_AGENT_HEADER = "user-agent"

_HTTPLIB_NO_TRACE_REQUEST = "_dd_no_trace"
DEFAULT_TIMEOUT = 2.0

COLLECTOR_MAX_SIZE_PER_SPAN = 100


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
    AI_GUARD = 13


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
