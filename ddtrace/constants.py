"""
This module contains constants used across ddtrace products.

Constants that should NOT be referenced by ddtrace users are marked with a leading underscore.
"""
_SAMPLING_PRIORITY_KEY = "_sampling_priority_v1"
_SAMPLING_AGENT_DECISION = "_dd.agent_psr"
_SAMPLING_RULE_DECISION = "_dd.rule_psr"
_SAMPLING_LIMIT_DECISION = "_dd.limit_psr"
_SINGLE_SPAN_SAMPLING_MECHANISM = "_dd.span_sampling.mechanism"
_SINGLE_SPAN_SAMPLING_RATE = "_dd.span_sampling.rule_rate"
_SINGLE_SPAN_SAMPLING_MAX_PER_SEC = "_dd.span_sampling.max_per_second"
_SINGLE_SPAN_SAMPLING_MAX_PER_SEC_NO_LIMIT = -1
_APM_ENABLED_METRIC_KEY = "_dd.apm.enabled"

_ORIGIN_KEY = "_dd.origin"
_USER_ID_KEY = "_dd.p.usr.id"
_HOSTNAME_KEY = "_dd.hostname"
_RUNTIME_FAMILY = "_dd.runtime_family"
ENV_KEY = "env"
VERSION_KEY = "version"
SERVICE_KEY = "service.name"
_BASE_SERVICE_KEY = "_dd.base_service"
SERVICE_VERSION_KEY = "service.version"
SPAN_KIND = "span.kind"
_SPAN_MEASURED_KEY = "_dd.measured"
_KEEP_SPANS_RATE_KEY = "_dd.tracer_kr"
_MULTIPLE_IP_HEADERS = "_dd.multiple-ip-headers"
_DJM_ENABLED_KEY = "_dd.djm.enabled"
_FILTER_KEPT_KEY = "_dd.filter.kept"

APPSEC_ENV = "DD_APPSEC_ENABLED"
_CONFIG_ENDPOINT_ENV = "_DD_CONFIG_ENDPOINT"
_CONFIG_ENDPOINT_RETRIES_ENV = "_DD_CONFIG_ENDPOINT_RETRIES"
_CONFIG_ENDPOINT_TIMEOUT_ENV = "_DD_CONFIG_ENDPOINT_TIMEOUT"
IAST_ENV = "DD_IAST_ENABLED"

MANUAL_DROP_KEY = "manual.drop"
MANUAL_KEEP_KEY = "manual.keep"

ERROR_MSG = "error.message"  # a string representing the error message
ERROR_TYPE = "error.type"  # a string representing the type of the error
ERROR_STACK = "error.stack"  # a human readable version of the stack.

PID = "process_id"

# Use this to explicitly inform the backend that a trace should be rejected and not stored.
USER_REJECT = -1
# Used by the builtin sampler to inform the backend that a trace should be rejected and not stored.
AUTO_REJECT = 0
# Used by the builtin sampler to inform the backend that a trace should be kept and stored.
AUTO_KEEP = 1
# Use this to explicitly inform the backend that a trace should be kept and stored.
USER_KEEP = 2
