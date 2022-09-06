SAMPLE_RATE_METRIC_KEY = "_sample_rate"
SAMPLING_PRIORITY_KEY = "_sampling_priority_v1"
ANALYTICS_SAMPLE_RATE_KEY = "_dd1.sr.eausr"
SAMPLING_AGENT_DECISION = "_dd.agent_psr"
SAMPLING_RULE_DECISION = "_dd.rule_psr"
SAMPLING_LIMIT_DECISION = "_dd.limit_psr"
_SINGLE_SPAN_SAMPLING_MECHANISM = "_dd.span_sampling.mechanism"
_SINGLE_SPAN_SAMPLING_RATE = "_dd.span_sampling.rule_rate"
_SINGLE_SPAN_SAMPLING_MAX_PER_SEC = "_dd.span_sampling.max_per_second"
_SINGLE_SPAN_SAMPLING_MAX_PER_SEC_NO_LIMIT = -1

ORIGIN_KEY = "_dd.origin"
USER_ID_KEY = "_dd.p.usr.id"
HOSTNAME_KEY = "_dd.hostname"
RUNTIME_FAMILY = "_dd.runtime_family"
ENV_KEY = "env"
VERSION_KEY = "version"
SERVICE_KEY = "service.name"
SERVICE_VERSION_KEY = "service.version"
SPAN_KIND = "span.kind"
SPAN_MEASURED_KEY = "_dd.measured"
KEEP_SPANS_RATE_KEY = "_dd.tracer_kr"
MULTIPLE_IP_HEADERS = "_dd.multiple-ip-headers"

APPSEC_ENABLED = "_dd.appsec.enabled"
APPSEC_JSON = "_dd.appsec.json"
APPSEC_EVENT_RULE_VERSION = "_dd.appsec.event_rules.version"
APPSEC_EVENT_RULE_ERRORS = "_dd.appsec.event_rules.errors"
APPSEC_EVENT_RULE_LOADED = "_dd.appsec.event_rules.loaded"
APPSEC_EVENT_RULE_ERROR_COUNT = "_dd.appsec.event_rules.error_count"
APPSEC_WAF_DURATION = "_dd.appsec.waf.duration"
APPSEC_WAF_DURATION_EXT = "_dd.appsec.waf.duration_ext"
APPSEC_WAF_TIMEOUTS = "_dd.appsec.waf.timeouts"
APPSEC_WAF_VERSION = "_dd.appsec.waf.version"


MANUAL_DROP_KEY = "manual.drop"
MANUAL_KEEP_KEY = "manual.keep"


ERROR_MSG = "error.msg"  # a string representing the error message
ERROR_TYPE = "error.type"  # a string representing the type of the error
ERROR_STACK = "error.stack"  # a human readable version of the stack.

PID = "system.pid"

# Use this to explicitly inform the backend that a trace should be rejected and not stored.
USER_REJECT = -1
# Used by the builtin sampler to inform the backend that a trace should be rejected and not stored.
AUTO_REJECT = 0
# Used by the builtin sampler to inform the backend that a trace should be kept and stored.
AUTO_KEEP = 1
# Use this to explicitly inform the backend that a trace should be kept and stored.
USER_KEEP = 2
