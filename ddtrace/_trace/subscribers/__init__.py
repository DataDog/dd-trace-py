"""Tracing-owned activation for manually instrumented integrations."""

from ddtrace._trace._registration import register_subscribers
from ddtrace._trace._registration import subscriber_bundles


# Preserve manual instrumentation when global tracer creation is deferred.
# The existing initialization hook and product startup own the web bundle.
register_subscribers(subscriber_bundles[1:])
