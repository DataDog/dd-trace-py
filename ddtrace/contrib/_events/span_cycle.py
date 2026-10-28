from enum import Enum


class SpanCycleEvents(Enum):
    # Dispatched by the tracing SpanLifecycleSubscriber as "<value>.<context identifier>" with (ctx,),
    # so products can take part in a span's lifecycle without depending on the tracer.
    SPAN_STARTING = "span.starting"
    SPAN_STARTED = "span.started"
    SPAN_FINISHING = "span.finishing"
