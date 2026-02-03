from dataclasses import dataclass
from typing import Any

from ddtrace._trace.trace_handlers import _finish_span
from ddtrace.internal.core.events import BaseEvent
from ddtrace.propagation.http import HTTPPropagator


@dataclass
class AioKafkaProduceCompleted(BaseEvent):
    event_name = "aiokafka.produce.completed"
    ctx: Any
    err_tuple: Any
    result: Any

    @classmethod
    def on_event(cls, event_instance, *additional_args):
        _finish_span(event_instance.ctx, event_instance.err_tuple)


@dataclass
class AioKafkaGetMany(BaseEvent):
    event_name = "aiokafka.getmany.messages"
    ctx: Any
    messages: Any
    config: Any

    @classmethod
    def on_event(cls, event_instance, *additional_args):
        span = event_instance.ctx.span
        messages = event_instance.messages

        for _, records in messages.items():
            for record in records:
                if event_instance.config.distributed_tracing_enabled and record.headers:
                    dd_headers = {
                        key: (val.decode("utf-8", errors="ignore") if isinstance(val, (bytes, bytearray)) else str(val))
                        for key, val in record.headers
                        if val is not None
                    }
                    context = HTTPPropagator.extract(dd_headers)

                    span.link_span(context)
