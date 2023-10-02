from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY

import traceback

def dsm_kafka_message_produce(instance, args, kwargs):
    from . import data_streams_processor as processor

    try:
        print("[dsm_kafka_message_produce] before")
        topic = core.get_item("kafka_topic")
        my_processor = processor()
        print(my_processor)
        print("[dsm_kafka_message_produce] after")
        pathway = my_processor.set_checkpoint(["direction:out", "topic:" + topic, "type:kafka"])
        print("[dsm_kafka_message_produce] done setting checkpoint")
        encoded_pathway = pathway.encode()
        headers = kwargs.get("headers", {})
        headers[PROPAGATION_KEY] = encoded_pathway
        kwargs["headers"] = headers
    except:
        traceback.print_exc()


def dsm_kafka_message_consume(instance, message):
    from . import data_streams_processor as processor

    headers = {header[0]: header[1] for header in (message.headers() or [])}
    topic = core.get_item("kafka_topic")
    group = instance._group_id

    ctx = processor().decode_pathway(headers.get(PROPAGATION_KEY, None))
    ctx.set_checkpoint(["direction:in", "group:" + group, "topic:" + topic, "type:kafka"])


if config._data_streams_enabled:
    core.on("kafka.produce.start", dsm_kafka_message_produce)
    core.on("kafka.consume.start", dsm_kafka_message_consume)
