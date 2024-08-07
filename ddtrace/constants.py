import sys


if sys.version_info >= (3, 8):
    from typing import Literal  # noqa:F401
else:
    from typing_extensions import Literal  # noqa:F401

# TODO: Deprecate and remove the SAMPLE_RATE_METRIC_KEY constant.
# This key enables legacy trace sampling support in the Datadog agent.
SAMPLE_RATE_METRIC_KEY: Literal["_sample_rate"] = "_sample_rate"
SAMPLING_PRIORITY_KEY: Literal["_sampling_priority_v1"] = "_sampling_priority_v1"
ANALYTICS_SAMPLE_RATE_KEY: Literal["_dd1.sr.eausr"] = "_dd1.sr.eausr"
SAMPLING_AGENT_DECISION: Literal["_dd.agent_psr"] = "_dd.agent_psr"
SAMPLING_RULE_DECISION: Literal["_dd.rule_psr"] = "_dd.rule_psr"
SAMPLING_LIMIT_DECISION: Literal["_dd.limit_psr"] = "_dd.limit_psr"
_SINGLE_SPAN_SAMPLING_MECHANISM: Literal["_dd.span_sampling.mechanism"] = "_dd.span_sampling.mechanism"
_SINGLE_SPAN_SAMPLING_RATE: Literal["_dd.span_sampling.rule_rate"] = "_dd.span_sampling.rule_rate"
_SINGLE_SPAN_SAMPLING_MAX_PER_SEC: Literal["_dd.span_sampling.max_per_second"] = "_dd.span_sampling.max_per_second"
_SINGLE_SPAN_SAMPLING_MAX_PER_SEC_NO_LIMIT = -1
_APM_ENABLED_METRIC_KEY: Literal["_dd.apm.enabled"] = "_dd.apm.enabled"

ORIGIN_KEY: Literal["_dd.origin"] = "_dd.origin"
USER_ID_KEY: Literal["_dd.p.usr.id"] = "_dd.p.usr.id"
HOSTNAME_KEY: Literal["_dd.hostname"] = "_dd.hostname"
RUNTIME_FAMILY: Literal["_dd.runtime_family"] = "_dd.runtime_family"
ENV_KEY: Literal["env"] = "env"
VERSION_KEY: Literal["version"] = "version"
SERVICE_KEY: Literal["service.name"] = "service.name"
BASE_SERVICE_KEY: Literal["_dd.base_service"] = "_dd.base_service"
SERVICE_VERSION_KEY: Literal["service.version"] = "service.version"
SPAN_KIND: Literal["span.kind"] = "span.kind"
SPAN_MEASURED_KEY: Literal["_dd.measured"] = "_dd.measured"
KEEP_SPANS_RATE_KEY: Literal["_dd.tracer_kr"] = "_dd.tracer_kr"
MULTIPLE_IP_HEADERS: Literal["_dd.multiple-ip-headers"] = "_dd.multiple-ip-headers"

APPSEC_ENV: Literal["DD_APPSEC_ENABLED"] = "DD_APPSEC_ENABLED"

IAST_ENV: Literal["DD_IAST_ENABLED"] = "DD_IAST_ENABLED"

MANUAL_DROP_KEY: Literal["manual.drop"] = "manual.drop"
MANUAL_KEEP_KEY: Literal["manual.keep"] = "manual.keep"

ERROR_MSG: Literal["error.message"] = "error.message"  # a string representing the error message
ERROR_TYPE: Literal["error.type"] = "error.type"  # a string representing the type of the error
ERROR_STACK: Literal["error.stack"] = "error.stack"  # a human readable version of the stack.

PID: Literal["process_id"] = "process_id"

# Use this to explicitly inform the backend that a trace should be rejected and not stored.
USER_REJECT = -1
# Used by the builtin sampler to inform the backend that a trace should be rejected and not stored.
AUTO_REJECT = 0
# Used by the builtin sampler to inform the backend that a trace should be kept and stored.
AUTO_KEEP = 1
# Use this to explicitly inform the backend that a trace should be kept and stored.
USER_KEEP = 2
