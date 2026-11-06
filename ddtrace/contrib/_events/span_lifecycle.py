from enum import Enum


class SpanLifecycleEvents(Enum):
    # Dispatched as "<value>.<context identifier>" with (ctx,), so products can
    # take part in a span's lifecycle without depending on the tracer. Existing
    # integrations can emit these phases directly while retaining their lifecycle owner.
    SPAN_STARTING = "span.starting"
    SPAN_STARTED = "span.started"
    SPAN_FINISHING = "span.finishing"
