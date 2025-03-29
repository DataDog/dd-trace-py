from ddtrace.vendor.debtcollector import deprecate

from .helpers import set_global_tracer
from .tracer import Tracer


deprecate(
    "The `ddtrace.opentracer` package is deprecated",
    message="The ddtrace library no longer supports the OpenTracing API. "
    "Use the OpenTelemetry API instead (`ddtrace.opentelemetry`).",
    removal_version="4.0.0",
)


__all__ = [
    "Tracer",
    "set_global_tracer",
]
