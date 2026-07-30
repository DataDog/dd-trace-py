from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# Tag keys that are always set automatically by the checkpoint helpers. User-supplied tags may not
# override them, otherwise the automatically assigned value (in particular ``direction``, which drives
# pathway continuity) could be superseded once tags are sorted in DataStreamsCtx.set_checkpoint.
_RESERVED_TAG_KEYS = frozenset(("type", "topic", "direction", "manual_checkpoint"))


def _filtered_extra_tags(tags):
    """Drop user-supplied tags that would corrupt the checkpoint.

    A tag is dropped (with a warning) when it uses a reserved key or when it contains a comma. Commas
    are unsupported because the processor serializes edge tags with a comma delimiter
    (``",".join(...)`` in on_checkpoint_creation, ``split(",")`` in _serialize_buckets), so a comma in
    a value would make the emitted tags disagree with the tags that were hashed.
    """
    filtered = []
    for tag in tags:
        key = tag.split(":", 1)[0]
        if key in _RESERVED_TAG_KEYS:
            log.warning(
                "data streams checkpoint tag %r uses reserved key %r and will be ignored; "
                "type, topic, direction and manual_checkpoint are assigned automatically",
                tag,
                key,
            )
            continue
        if "," in tag:
            log.warning(
                "data streams checkpoint tag %r contains an unsupported comma and will be ignored",
                tag,
            )
            continue
        filtered.append(tag)
    return filtered


def set_consume_checkpoint(typ, source, carrier_get, manual_checkpoint=True, tags=None):
    """
    :param typ: The type of the checkpoint, usually the streaming technology being used.
        Examples include kafka, kinesis, sns etc. (str)
    :param source: The source of data. This can be a topic, exchange or stream name. (str)
    :param carrier_get: A function used to extract context from the carrier (function (str) -> str)
    :param manual_checkpoint: Whether this checkpoint was manually set. Keep true if manually instrumenting.
        Manual instrumentation always overrides automatic instrumentation in the case a call is both
        manually and automatically instrumented. (bool)
    :param tags: Additional edge tags to associate with this checkpoint, on top of the ``type``,
        ``topic``, ``direction`` and ``manual_checkpoint`` tags set automatically. Each entry should be
        a ``"key:value"`` string, e.g. ``["exchange:my-bus"]``. Tags that use one of the reserved keys
        (``type``, ``topic``, ``direction``, ``manual_checkpoint``) or that contain a comma are ignored
        with a warning. (Optional[list[str]])

    :returns DataStreamsCtx | None
    """
    processor = data_streams_processor()
    if processor:
        processor.decode_pathway_b64(carrier_get(PROPAGATION_KEY_BASE_64))
        edge_tags = ["type:" + typ, "topic:" + source, "direction:in"]
        if manual_checkpoint:
            edge_tags.append("manual_checkpoint:true")
        if tags:
            edge_tags.extend(_filtered_extra_tags(tags))
        return processor.set_checkpoint(edge_tags)


def set_produce_checkpoint(typ, target, carrier_set, tags=None):
    """
    :param typ: The type of the checkpoint, usually the streaming technology being used. Examples include
        kafka, kinesis, sns etc. (str)
    :param target: The destination to which the data is being sent. For instance: topic, exchange or
        stream name. (str)
    :param carrier_set: A function used to inject the context into the carrier (function (str, str) -> None)
    :param tags: Additional edge tags to associate with this checkpoint, on top of the ``type``,
        ``topic``, ``direction`` and ``manual_checkpoint`` tags set automatically. Each entry should be
        a ``"key:value"`` string, e.g. ``["exchange:my-bus"]``. Tags that use one of the reserved keys
        (``type``, ``topic``, ``direction``, ``manual_checkpoint``) or that contain a comma are ignored
        with a warning. (Optional[list[str]])

    :returns DataStreamsCtx | None
    """
    processor = data_streams_processor()
    if processor:
        edge_tags = ["type:" + typ, "topic:" + target, "direction:out", "manual_checkpoint:true"]
        if tags:
            edge_tags.extend(_filtered_extra_tags(tags))
        pathway = processor.set_checkpoint(edge_tags)
        if pathway is not None:
            carrier_set(PROPAGATION_KEY_BASE_64, pathway.encode_b64())
        return pathway
