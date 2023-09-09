from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY
from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.utils import get_argument_value
from ddtrace.pin import Pin
from queue import Queue
from pprint import pprint

_dsm_queue = Queue()
_processor = None

def processor():
    global _processor
    if not _processor:
        _processor = DataStreamsProcessor("http://localhost:9126")
    return _processor

def kafka_message_producer_dsm_start(func, instance, args, kwargs, event_uuid, event_time):
    topic = get_argument_value(args, kwargs, 0, "topic") or ""
    headers = kwargs.get("headers", {})
    pathway = processor().set_checkpoint(["direction:out", "topic:" + topic, "type:kafka"])
    encoded_pathway = pathway.encode()
    headers[PROPAGATION_KEY] = encoded_pathway
    kwargs["headers"] = headers

    core.set_item(event_uuid, event_uuid)
    core.set_item(event_time, event_time)

    message = {
        "direction": "out",
        "type": "kafka",
        "topic": topic,
        "time": event_time,
    }
    _dsm_queue.put(message)
    core.dispatch("kafka.produce.start.process", [])


_previous_message = None
def process_from_queue():
    message = _dsm_queue.get()
    import pdb; pdb.set_trace()
    pprint(message)
    
def dsm_generate_pathway(func, instance, args, kwargs, event_uuid, event_time):
    start_time = event_time

if True: # TODO: config._data_streams_enabled
    core.on("kafka.produce.start", kafka_message_producer_dsm_start)
    core.on("kafka.produce.start.process", process_from_queue)
