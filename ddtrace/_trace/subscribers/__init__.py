"""Tracing-owned activation for manually instrumented integrations."""

from ddtrace._trace._registration import MANUAL_SUBSCRIBER_BUNDLES
from ddtrace._trace._registration import register_subscribers


# Preserve manual instrumentation when global tracer creation is deferred.
# The existing initialization hook and product startup own the web and exception bundles.
register_subscribers(MANUAL_SUBSCRIBER_BUNDLES)
