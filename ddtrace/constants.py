"""
This module contains constants exposed to ddtrace users.
"""
ENV_KEY = "env"
VERSION_KEY = "version"
SERVICE_KEY = "service.name"
SERVICE_VERSION_KEY = "service.version"
SPAN_KIND = "span.kind"

APPSEC_ENV = "DD_APPSEC_ENABLED"
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
