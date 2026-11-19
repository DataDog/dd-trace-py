"""Tracing-owned activation for manually instrumented integrations."""

from wrapt.importer import when_imported

from ddtrace._trace._registration import MANUAL_SUBSCRIBER_BUNDLES
from ddtrace._trace._registration import RAY_SUBSCRIBERS
from ddtrace._trace._registration import register_subscribers


# Preserve manual instrumentation when global tracer creation is deferred.
# The existing initialization hook and product startup own the web and exception bundles.
register_subscribers(MANUAL_SUBSCRIBER_BUNDLES)


# Ray's subscribers import SDK helpers. Wait for the integration module instead
# of loading the optional SDK during tracing startup or manual initialization.
when_imported("ddtrace.contrib.internal.ray.patch")(lambda _: RAY_SUBSCRIBERS.register())
