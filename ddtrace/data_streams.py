import ddtrace
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64


def set_consume_checkpoint(typ, source, carrier_get, manual_checkpoint=True):
    """
    :param typ: The type of the checkpoint, usually the streaming technology being used.
        Examples include kafka, kinesis, sns etc. (str)
    :param source: The source of data. This can be a topic, exchange or stream name. (str)
    :param carrier_get: A function used to extract context from the carrier (function (str) -> str)
    :param manual_checkpoint: Whether this checkpoint was manually set. Keep true if manually instrumenting.
        Manual instrumentation always overrides automatic instrumentation in the case a call is both
        manually and automatically instrumented. (bool)

    :returns DataStreamsCtx | None
    """
    if ddtrace.config._data_streams_enabled:
        processor = ddtrace.tracer.data_streams_processor
        processor.decode_pathway_b64(carrier_get(PROPAGATION_KEY_BASE_64))
        tags = ["type:" + typ, "topic:" + source, "direction:in"]
        if manual_checkpoint:
            tags.append("manual_checkpoint:true")
        return processor.set_checkpoint(tags)


def set_produce_checkpoint(typ, target, carrier_set):
    """
    :param typ: The type of the checkpoint, usually the streaming technology being used. Examples include
        kafka, kinesis, sns etc. (str)
    :param target: The destination to which the data is being sent. For instance: topic, exchange or
        stream name. (str)
    :param carrier_set: A function used to inject the context into the carrier (function (str, str) -> None)

    :returns DataStreamsCtx | None
    """
    if ddtrace.config._data_streams_enabled:
        pathway = ddtrace.tracer.data_streams_processor.set_checkpoint(
            ["type:" + typ, "topic:" + target, "direction:out", "manual_checkpoint:true"]
        )
        if pathway is not None:
            carrier_set(PROPAGATION_KEY_BASE_64, pathway.encode_b64())
        return pathway
