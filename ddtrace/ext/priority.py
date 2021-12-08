"""
Priority is a hint given to the backend so that it knows which traces to reject or kept.
In a distributed context, it should be set before any context propagation (fork, RPC calls) to be effective.

For example:

from ddtrace.ext.priority import USER_REJECT, USER_KEEP

context = tracer.context_provider.active()
# Indicate to not keep the trace
context.sampling_priority = USER_REJECT

# Indicate to keep the trace
span.context.sampling_priority = USER_KEEP
"""
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from ddtrace.internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.ext.priority",
    message="Use `ddtrace.constants` module instead",
    version="1.0.0",
)

__all__ = ["USER_REJECT", "AUTO_REJECT", "AUTO_KEEP", "USER_KEEP"]
