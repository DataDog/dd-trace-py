from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal._rand import rand64bits as _rand64bits
from ddtrace.internal._rand import rand128bits as _rand128bits
from ddtrace import config

from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import ArgumentError
from pprint import pprint

log = get_logger(__name__)


class TraceInstrumentation:
    pass


############


def generic_span_info():
    pass

def outbound_span_info():
    pass

def message_producer_span_info():
    pass

def kafka_message_producer_span_info(func, instance, args, kwargs, event_uuid, event_time):
    host_list = instance._dd_bootstrap_servers or []
    topic = get_argument_value(args, kwargs, 0, "topic") or ""
    try:
        value = get_argument_value(args, kwargs, 1, "value")
    except ArgumentError:
        value = None
    message_key = kwargs.get("key", "")
    partition = kwargs.get("partition", -1)

    core.set_item("value", value)
    core.set_item("host_list", host_list)
    core.set_item("topic", topic)
    core.set_item("message_key", message_key)
    core.set_item("partition", partition)
    core.set_item("rate", config.kafka.get_analytics_sample_rate)

    core.dispatch("kafka.produce.start.span", [event_uuid, event_time])
    

_SPANS = dict()

def kafka_producer_start(identifier, timestamp):
    partial_span = {
        "name": "kafka.produce",  # need to schematize?
        "service": "kafka",    # composite, should calculate.  Also need once?
        "resource": "kafka.produce",
        "trace_id": 0, # TODO
        "span_id": _rand64bits(),  # Add support for 128 bit
        "parent_id": 0, #TODO 
        "type": "worker",
        "error": 0, # TODO
        "meta": {
            "_dd.p.dm": "-0",  # TODO
            "component": "kafka",
            "kafka.message_key": core.get_item("message_key"),
            "kafka.tombstone": (core.get_item("value") is not None),
            "kafka.topic": core.get_item("topic"),
            "language": "python",
            "ANALYTICS_SAMPLE_RATE_KEY": None, # TODO
            "messaging.kafka.bootstrap.servers": core.get_item("bootstrap_servers"),
            "messaging.system": "kafka",
            "runtime-id": "TODO", # TODO
            "span.kind": "producer",
        },
        "metrics": {
            # TODO
            "_dd.measured": 1, # TODO - and I feel like this is a metrics decision?
            "kafka.partition": core.get_item("partition")
        },
        "duration": None, # Finished later
        "start": timestamp 

    }
    import pdb; pdb.set_trace()
    pprint(partial_span)
    _SPANS[identifier] = partial_span

def kafka_producer_finish(identifier, timestamp):
    try:
        partial_span = _SPANS[identifier]
    except KeyError:
        return  # Key doesn't exist?
        
    partial_span["duration"] = timestamp - partial_span["start"]
    finished_span = partial_span
    import pdb; pdb.set_trace()
    pprint(finished_span)
    del _SPANS[identifier]



core.on("kafka.produce.start", kafka_message_producer_span_info)
core.on("kafka.produce.start.span", kafka_producer_start)
core.on("kafka.produce.finish", kafka_message_producer_span_info)
core.on("kafka.produce.finish.span", kafka_producer_finish)
