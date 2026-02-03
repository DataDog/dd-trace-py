
from dataclasses import dataclass
from typing import Any
from ddtrace.internal.core.events import BaseEvent
from ddtrace._trace.trace_handlers import _finish_span

@dataclass
class AioKafkaProduceCompleted(BaseEvent):
    event_name ="aiokafka.produce.completed"
    ctx: Any
    err_tuple: Any
    result: Any

    @classmethod
    def on_event(cls, event_instance, *additional_args):
        _finish_span(event_instance.ctx, event_instance.err_tuple)