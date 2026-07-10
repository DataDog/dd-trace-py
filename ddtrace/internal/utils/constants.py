# used by _trace and multiple contribs
LOG_ATTR_TRACE_ID = "dd.trace_id"
LOG_ATTR_SPAN_ID = "dd.span_id"
LOG_ATTR_ENV = "dd.env"
LOG_ATTR_VERSION = "dd.version"
LOG_ATTR_SERVICE = "dd.service"
LOG_ATTR_VALUE_ZERO = "0"
LOG_ATTR_VALUE_EMPTY = ""

FLASK_ENDPOINT = "flask.endpoint"
FLASK_VIEW_ARGS = "flask.view_args"
FLASK_URL_RULE = "flask.url_rule"
FLASK_RESOURCE_FULL = "flask.resource.full"

STATUS_403_TYPE_AUTO = {"status_code": 403, "type": "auto"}
DEFAULT_SERVICE_NAME = "unnamed-python-service"

MAX_UINT_64BITS = (1 << 64) - 1
MIN_INT_64BITS = -(2**63)
MAX_INT_64BITS = 2**63 - 1

_HTTPLIB_NO_TRACE_REQUEST = "_dd_no_trace"
HTTP_REQUEST_BLOCKED = "http.request.blocked"


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
