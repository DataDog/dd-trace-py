import time

from opentelemetry import trace

import ddtrace
from ddtrace.opentelemetry import OtelTracerProvider


provider = OtelTracerProvider()
trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)

# # Works
with ddtrace.tracer.trace("ddtrace-single-context") as root:
    with ddtrace.tracer.trace("ddtrace-child") as dd_child:
        with tracer.start_as_current_span("otel-child") as child:
            time.sleep(0.02)

    with tracer.start_as_current_span("otel-child") as child:
        with ddtrace.tracer.trace("ddtrace-child") as dd_child:
            time.sleep(0.04)

# # Works Now!!!
with ddtrace.tracer.trace("ddtrace-mixed-context") as root:
    with ddtrace.tracer.trace("ddtrace-child") as dd_child1:
        time.sleep(0.04)

    with tracer.start_as_current_span("otel-child") as child2:
        with ddtrace.tracer.trace("ddtrace-child") as dd_child:
            # This gets parented to the other otel-child
            with tracer.start_as_current_span("otel-inner") as child3:
                time.sleep(0.02)
