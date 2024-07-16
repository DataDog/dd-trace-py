import ddtrace
from ddtrace.internal.datastreams.processor import DsmPathwayCodec


def set_consume_checkpoint(typ, source, carrier_get):
    """
    :param typ: The type of the checkpoint, usually the streaming technology being used.
        Examples include kafka, kinesis, sns etc. (str)
    :param source: The source of data. This can be a topic, exchange or stream name. (str)
    :param carrier_get: A function used to extract context from the carrier (function (str) -> str)

    :returns DataStreamsCtx | None
    """
    if ddtrace.config._data_streams_enabled:
        processor = ddtrace.tracer.data_streams_processor
        ctx = DsmPathwayCodec.decode(carrier_get, processor)
        return ctx.set_checkpoint(["type:" + typ, "topic:" + source, "direction:in", "manual_checkpoint:true"])


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
        ctx = ddtrace.tracer.data_streams_processor.set_checkpoint(
            ["type:" + typ, "topic:" + target, "direction:out", "manual_checkpoint:true"]
        )
        DsmPathwayCodec.encode(ctx, carrier_set)
        return ctx
