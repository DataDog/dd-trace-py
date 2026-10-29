from __future__ import annotations

import base64
import json
from typing import Any
from typing import Optional
from typing import cast
from urllib import parse

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.datastreams.pipelines import get_pipelines_reporter
from ddtrace.internal.datastreams.processor import DsmPathwayCodec
from ddtrace.internal.datastreams.utils import _calculate_byte_size
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def get_queue_name(params: dict[str, Any]) -> str:
    """
    :params: contains the params for the current botocore action

    Return the name of the queue given the params
    """
    queue_url = params["QueueUrl"]
    url = parse.urlparse(queue_url)
    return url.path.rsplit("/", 1)[-1]


def get_topic_arn(params: dict[str, Any]) -> str:
    """
    :params: contains the params for the current botocore action

    Return the name of the topic given the params
    """
    sns_arn = params["TopicArn"]
    return sns_arn


def get_stream(params: dict[str, Any]) -> str:
    """
    :params: contains the params for the current botocore action

    Return the name of the stream given the params
    """
    stream = params.get("StreamARN", params.get("StreamName", ""))
    return stream


def get_eventbridge_bus_name(event_entry: dict[str, Any]) -> str:
    """
    :event_entry: contains the EventBridge entry for the current botocore action

    Return the EventBridge bus name for a PutEvents entry.
    """
    return event_entry.get("EventBusName", "default")


def get_eventbridge_detail_type(event_entry: dict[str, Any]) -> str:
    """
    :event_entry: contains the EventBridge entry for the current botocore action

    Return the EventBridge detail type for a PutEvents entry.
    """
    return event_entry.get("DetailType", "")


def eventbridge_edge_tags(event_entry: dict[str, Any]) -> list[str]:
    """
    :event_entry: contains the EventBridge entry for the current botocore action

    Return the DSM edge tags for a PutEvents entry.
    """
    return [
        "direction:out",
        f"exchange:{get_eventbridge_bus_name(event_entry)}",
        f"topic:{get_eventbridge_detail_type(event_entry)}",
        "type:eventbridge",
    ]


def set_produce_checkpoint(trace_data: dict[str, Any], pathway_tags: list[str], payload_size: int) -> None:
    from . import data_streams_processor as processor

    if (p := processor()) is not None:
        ctx = p.set_checkpoint(pathway_tags, payload_size=payload_size)
        DsmPathwayCodec.encode(ctx, trace_data)


def inject_context(
    trace_data: dict[str, Any],
    endpoint_service: str,
    dsm_identifier: Optional[str],
    message: dict[str, Any],
) -> None:
    """
    :endpoint_service: the name  of the service (i.e. 'sns', 'sqs', 'kinesis')
    :dsm_identifier: the identifier for the topic/queue/stream/etc

    Set the data streams monitoring checkpoint and inject context to carrier
    """
    path_type = f"type:{endpoint_service}"

    payload_size = 0
    if endpoint_service == "sqs":
        payload_size = calculate_sqs_payload_size(message, trace_data)
    elif endpoint_service == "sns":
        payload_size = calculate_sns_payload_size(message, trace_data)
    elif endpoint_service == "kinesis":
        payload_size = calculate_kinesis_payload_size(message, trace_data)
    elif endpoint_service == "eventbridge":
        payload_size = calculate_eventbridge_payload_size(message, trace_data)

    if not dsm_identifier:
        log.debug("pathway being generated with unrecognized service: %r", dsm_identifier)
    set_produce_checkpoint(
        trace_data,
        ["direction:out", f"topic:{dsm_identifier}", path_type],
        payload_size,
    )


def calculate_sqs_payload_size(message: dict[str, Any], trace_data: Optional[Any] = None) -> int:
    payload_size = _calculate_byte_size(message.get("MessageBody", ""))
    payload_size += _calculate_byte_size(message.get("MessageAttributes", {}))
    if trace_data:
        # we should count datadog message attributes which aren't yet added to the message
        payload_size += _calculate_byte_size({"_datadog": {"DataType": "String", "StringValue": trace_data}})
    payload_size += _calculate_byte_size(message.get("MessageSystemAttributes", {}))
    payload_size += _calculate_byte_size(message.get("MessageGroupId", ""))
    return payload_size


def calculate_sns_payload_size(message: dict[str, Any], trace_data: Any) -> int:
    payload_size = _calculate_byte_size(message.get("Message", ""))
    payload_size += _calculate_byte_size(message.get("MessageAttributes", {}))
    # we should count datadog message attributes which aren't yet added to the message
    payload_size += _calculate_byte_size({"_datadog": {"DataType": "Binary", "BinaryValue": trace_data}})
    payload_size += _calculate_byte_size(message.get("Subject", ""))
    payload_size += _calculate_byte_size(message.get("MessageGroupId", ""))
    return payload_size


def calculate_kinesis_payload_size(message: dict[str, Any], trace_data: Optional[Any] = None) -> int:
    payload_size = _calculate_byte_size(message.get("Data", ""))
    payload_size += _calculate_byte_size(message.get("ExplicitHashKey", ""))
    payload_size += _calculate_byte_size(message.get("PartitionKey", ""))
    # if we don't have trace context data its because we are receiving and its within the message
    if trace_data:
        # we should count datadog message attributes which aren't yet added to the message
        payload_size += _calculate_byte_size({"_datadog": trace_data})
    return payload_size


def calculate_eventbridge_payload_size(message: dict[str, Any], trace_data: Optional[Any] = None) -> int:
    payload_size = _calculate_byte_size(message)
    if trace_data:
        # we should count datadog detail fields which aren't yet added to the serialized Detail payload
        payload_size += _calculate_byte_size({"_datadog": trace_data})
    return payload_size


def handle_kinesis_produce(
    ctx: Any,
    stream: str,
    dd_ctx_json: dict[str, Any],
    record: dict[str, Any],
    *args: Any,
) -> None:
    if config._data_streams_enabled:
        if "_datadog" not in dd_ctx_json:
            dd_ctx_json["_datadog"] = {}
        if stream:  # If stream ARN / stream name isn't specified, we give up (it is not a required param)
            inject_context(dd_ctx_json["_datadog"], "kinesis", stream, record)


def handle_eventbridge_produce(
    ctx: Any,
    span: Any,
    endpoint_service: str,
    trace_data: dict[str, Any],
    request_params: dict[str, Any],
    event_entry: Optional[dict[str, Any]] = None,
) -> None:
    # EventBridge DSM injection is normally dispatched once per PutEvents entry, so
    # `event_entry` is the specific event being mutated while `request_params` is the
    # full PutEvents payload. Fall back to `request_params` for any caller that only
    # passes a single message-like object.
    if not event_entry:
        event_entry = request_params
    payload_size = calculate_eventbridge_payload_size(event_entry, trace_data)
    set_produce_checkpoint(trace_data, eventbridge_edge_tags(event_entry), payload_size)


def handle_sqs_sns_produce(
    ctx: Any,
    span: Any,
    endpoint_service: str,
    trace_data: dict[str, Any],
    params: dict[str, Any],
    message: Optional[dict[str, Any]] = None,
) -> None:
    # if a message wasn't included, that means that the message is in the params object
    if not message:
        message = params
    dsm_identifier = None
    if endpoint_service == "sqs":
        dsm_identifier = get_queue_name(params)
    elif endpoint_service == "sns":
        dsm_identifier = get_topic_arn(params)
    inject_context(trace_data, endpoint_service, dsm_identifier, message)


def handle_sqs_prepare(params: dict[str, Any]) -> None:
    if "MessageAttributeNames" not in params:
        params.update({"MessageAttributeNames": ["_datadog"]})
    elif "_datadog" not in params["MessageAttributeNames"]:
        params.update({"MessageAttributeNames": list(params["MessageAttributeNames"]) + ["_datadog"]})


def get_datastreams_context(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Formats we're aware of:
        - message.MessageAttributes._datadog.StringValue (SQS)
        - message.Body.MessageAttributes._datadog.Value.decode() (SNS -> SQS)
        - message.MessageAttributes._datadog.BinaryValue.decode() (SNS -> SQS, raw)
        - message.messageAttributes._datadog.stringValue (SQS -> lambda)
    """
    context_json = None
    message_body: Any = message
    message_attributes = message.get("MessageAttributes") or message.get("messageAttributes")

    # Fall back to the body for SNS -> SQS notifications, which carry their attributes there.
    if not (message_attributes and "_datadog" in message_attributes):
        try:
            body = message.get("Body")
            if body:
                message_body = json.loads(body)
        except (ValueError, TypeError):
            log.debug("Unable to parse message body as JSON, treat as non-json")
        if not isinstance(message_body, dict):
            log.debug("DataStreams skipped message with non-object JSON body: %r", message)
            return None
        message_attributes = message_body.get("MessageAttributes") or message_body.get("messageAttributes")

    if not message_attributes or "_datadog" not in message_attributes:
        log.debug("DataStreams skipped message: %r", message)
        return None

    datadog_attr = message_attributes["_datadog"]

    if message_body.get("Type") == "Notification":
        # This is potentially a DSM SNS notification
        if datadog_attr.get("Type") == "Binary":
            context_json = json.loads(base64.b64decode(datadog_attr["Value"]).decode())
    elif "StringValue" in datadog_attr:
        # The message originated from SQS
        context_json = json.loads(datadog_attr["StringValue"])
    elif "stringValue" in datadog_attr:
        # The message originated from Lambda
        context_json = json.loads(datadog_attr["stringValue"])
    elif "BinaryValue" in datadog_attr:
        # Raw message delivery
        context_json = json.loads(datadog_attr["BinaryValue"].decode())
    else:
        log.debug("DataStreams did not handle message: %r", message)

    return context_json


def handle_sqs_receive(_, params: dict[str, Any], result: dict[str, Any], *args: Any) -> None:
    from . import data_streams_processor as processor

    queue_name = get_queue_name(params)

    for message in result.get("Messages", []):
        try:
            context_json = get_datastreams_context(message)
            payload_size = calculate_sqs_payload_size(message)

            if (p := processor()) is not None:
                ctx = DsmPathwayCodec.decode(cast(dict[Any, Any], context_json), p)
                ctx.set_checkpoint(
                    ["direction:in", "topic:" + queue_name, "type:sqs"],
                    payload_size=payload_size,
                )
        except Exception:
            log.debug(
                "Error receiving SQS message with data streams monitoring enabled",
                exc_info=True,
            )


class StreamMetadataNotFound(Exception):
    pass


def record_data_streams_path_for_kinesis_stream(
    params: dict[str, Any],
    time_estimate: float,
    context_json: Any,
    record: dict[str, Any],
) -> None:
    from . import data_streams_processor as processor

    stream = get_stream(params)

    if not stream:
        log.debug(
            "Unable to determine StreamARN and/or StreamName for request with params: %r",
            params,
        )
        raise StreamMetadataNotFound()

    payload_size = calculate_kinesis_payload_size(record)
    if (p := processor()) is not None:
        ctx = DsmPathwayCodec.decode(context_json, p)
        # The arrival time is only a fallback start for records that carry no upstream DSM context.
        start_override = None if ctx.hash else time_estimate
        ctx.set_checkpoint(
            ["direction:in", "topic:" + stream, "type:kinesis"],
            edge_start_sec_override=start_override,
            pathway_start_sec_override=start_override,
            payload_size=payload_size,
        )


def handle_kinesis_receive(
    _,
    params: dict[str, Any],
    time_estimate: float,
    context_json: Any,
    record: dict[str, Any],
    *args: Any,
) -> None:
    try:
        record_data_streams_path_for_kinesis_stream(params, time_estimate, context_json, record)
    except Exception:
        log.warning("Failed to report data streams monitoring info for kinesis", exc_info=True)


# ---------------------------------------------------------------------------
# Automatic DSM pipelines (DSM2-531)
#
# Separate from the DSM stats handlers above: these handlers record only which
# topics this service produces to and consumes from, plus in-process
# produce-inside-consume links, even when DD_DATA_STREAMS_ENABLED is false.
# They call the pinned recording API in ddtrace.internal.datastreams.pipelines
# (queue_type in {kafka, sqs, sns, kinesis, eventbridge, pubsub, rabbitmq},
# cluster "" when unknown, bare topic name, group "" for produce and when
# unknown). Every handler bails out on its first line when pipelines reporting
# is disabled, which makes the whole feature a no-op then (T-13: never raise).
# ---------------------------------------------------------------------------


def _parse_json_object(text: Any) -> Optional[dict[str, Any]]:
    """Parse text as a JSON object, or return None for anything else."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def classify_sqs_message_origin(body: Any) -> Optional[tuple[str, str]]:
    """Classify an SQS message's origin from its body envelope metadata only.

    Returns ("sns", TopicArn) for SNS notification envelopes and
    ("eventbridge", detail-type) for EventBridge envelopes, or None when the
    body carries neither, in which case the message is attributed to the SQS
    queue itself. No message ids, no propagation headers. The substring
    checks run on the raw body before any JSON parsing.
    """
    if not body:
        return None
    parsed = None
    # Classifying the SNS envelope here is what makes an SNS -> SQS
    # subscription a single pipeline: the publisher's produce side is
    # ("sns", TopicArn) and the subscriber's consume side is classified as
    # ("sns", TopicArn) instead of ("sqs", queue name), so both intern to the
    # same (queue_type, topic) and the backend joins producer and consumer on
    # it. The same holds for EventBridge via the detail-type.
    if "TopicArn" in body:
        parsed = _parse_json_object(body)
        topic_arn = parsed.get("TopicArn") if parsed is not None else None
        if isinstance(topic_arn, str) and topic_arn:
            return ("sns", topic_arn)
    if '"detail-type"' in body:
        if parsed is None:
            parsed = _parse_json_object(body)
        detail_type = parsed.get("detail-type") if parsed is not None else None
        if isinstance(detail_type, str) and detail_type:
            return ("eventbridge", detail_type)
    return None


_sqs_queue_origins: dict[str, tuple[str, str]] = {}
# queue name -> (queue_type, topic) origin verdict for SQS queues whose message
# bodies carry an SNS or EventBridge envelope. The cache only short-circuits
# the JSON re-parsing of marker-carrying bodies: the cheap substring check runs
# for every message, so bodies without envelope markers are always attributed
# to the queue itself and a mixed batch keeps per-message sides (the last
# message's side wins as the thread's context). A queue subscribed to multiple
# topics (or to both SNS and EventBridge) attributes marker-carrying messages
# to the first-seen origin (accepted trade-off).


def _sqs_consume_side(queue_name: str, body: Any) -> tuple[str, str]:
    """Return the (queue_type, topic) consume side for one SQS message."""
    if body and ("TopicArn" in body or '"detail-type"' in body):
        origin = _sqs_queue_origins.get(queue_name)
        if origin is not None:
            return origin
        origin = classify_sqs_message_origin(body)
        if origin is not None:
            _sqs_queue_origins[queue_name] = origin
            return origin
    return ("sqs", queue_name)


def handle_pipelines_sqs_sns_produce(
    ctx: Any,
    span: Any,
    endpoint_service: str,
    trace_data: dict[str, Any],
    params: dict[str, Any],
    message: Optional[dict[str, Any]] = None,
    *args: Any,
) -> None:
    # Produce side for SQS SendMessage/SendMessageBatch (endpoint "sqs";
    # topic = queue name, the last path segment of QueueUrl) and SNS
    # Publish/PublishBatch (endpoint "sns"; topic = TopicArn). The event fires
    # once per batch entry; the reporter interns identical sides, so a batch
    # records a single side. Fires before the send, which is fine: the side
    # only describes which topic the service writes to.
    reporter = get_pipelines_reporter()
    if reporter is None:
        return
    try:
        if endpoint_service == "sqs":
            reporter.on_produce("sqs", "", get_queue_name(params))
        elif endpoint_service == "sns":
            reporter.on_produce("sns", "", get_topic_arn(params))
    except Exception:
        log.debug("pipelines: failed to record SQS/SNS produce side", exc_info=True)


def handle_pipelines_kinesis_produce(ctx: Any, stream: str, dd_ctx_json: Any, record: Any, *args: Any) -> None:
    # Produce side for Kinesis PutRecord/PutRecords; topic is the stream
    # identifier exactly as the patch derived it (StreamARN preferred, else
    # StreamName), matching the consume side below so both intern to the same
    # topic. Give up when neither is given (not a required param).
    reporter = get_pipelines_reporter()
    if reporter is None:
        return
    try:
        if stream:
            reporter.on_produce("kinesis", "", stream)
    except Exception:
        log.debug("pipelines: failed to record kinesis produce side", exc_info=True)


def handle_pipelines_eventbridge_produce(
    ctx: Any,
    span: Any,
    endpoint_service: str,
    trace_data: dict[str, Any],
    request_params: Optional[dict[str, Any]],
    event_entry: Optional[dict[str, Any]] = None,
    *args: Any,
) -> None:
    # Produce side for EventBridge PutEvents; topic is the entry's detail-type
    # (see eventbridge_edge_tags above). This event is only usable when the
    # contrib passes the entry: with DD_DATA_STREAMS_ENABLED=false it strips
    # params and the entry from the payload (see update_eventbridge_detail in
    # contrib/internal/botocore/utils.py), so the DSM-off case is covered by
    # handle_pipelines_api_call_started instead. When both fire, the reporter
    # interns the identical side once.
    reporter = get_pipelines_reporter()
    if reporter is None:
        return
    try:
        entry = event_entry if event_entry is not None else request_params
        if entry is None:
            return
        detail_type = get_eventbridge_detail_type(entry)
        if detail_type:
            reporter.on_produce("eventbridge", "", detail_type)
    except Exception:
        log.debug("pipelines: failed to record EventBridge produce side", exc_info=True)


def handle_pipelines_api_call_started(ctx: Any, *args: Any) -> None:
    # Fallback produce side for EventBridge PutEvents in the DSM-off mode of
    # this feature, where botocore.eventbridge.update_messages carries no
    # params/entry (see handle_pipelines_eventbridge_produce). The generic
    # api-call-started context always carries endpoint, operation and params.
    # This event fires for every botocore call on the fallback patch path, so
    # the endpoint/operation check must stay cheap; it is only reached when
    # pipelines reporting is enabled.
    reporter = get_pipelines_reporter()
    if reporter is None:
        return
    try:
        if ctx.get_item("endpoint_name") != "events" or ctx.get_item("operation") != "PutEvents":
            return
        params = ctx.get_item("params") or {}
        for entry in params.get("Entries", []):
            detail_type = get_eventbridge_detail_type(entry)
            if detail_type:
                reporter.on_produce("eventbridge", "", detail_type)
    except Exception:
        log.debug("pipelines: failed to record EventBridge produce side", exc_info=True)


def handle_pipelines_sqs_receive(_, params: dict[str, Any], result: Optional[dict[str, Any]], *args: Any) -> None:
    # Consume sides for SQS ReceiveMessage, one per received message (interned
    # to one side per origin). The event fires when the instrumented call
    # returns, before the user processes the message, so each side is set as
    # the thread's current consumed-message context with set_current_consume
    # (the context-until-replaced variant). The LAST message's side wins as
    # the context, which is correct for a batch from one queue (all messages
    # are the same side); in a mixed batch (a queue also fed by SNS/EventBridge)
    # every side is still recorded, but only the last message's side stays
    # current. An empty poll records nothing and leaves any earlier context in
    # place. group is "" for SQS (no consumer groups).
    reporter = get_pipelines_reporter()
    if reporter is None:
        return
    try:
        messages = result.get("Messages") if isinstance(result, dict) else None
        if not messages:
            return
        queue_name = get_queue_name(params)
        for message in messages:
            queue_type, topic = _sqs_consume_side(queue_name, message.get("Body"))
            reporter.set_current_consume(queue_type, "", topic, "")
    except Exception:
        log.debug("pipelines: failed to record SQS consume side", exc_info=True)


def handle_pipelines_kinesis_receive(
    _,
    params: dict[str, Any],
    time_estimate: Any,
    context_json: Any,
    record: Any,
    *args: Any,
) -> None:
    # Consume side for Kinesis GetRecords; topic is derived exactly like
    # handle_kinesis_receive above (StreamARN preferred, else StreamName) so it
    # matches the produce side. The event fires once per record; the sides
    # intern to one and the last set_current_consume wins as the thread's
    # current context. group is "" for Kinesis.
    reporter = get_pipelines_reporter()
    if reporter is None:
        return
    try:
        stream = get_stream(params)
        if stream:
            reporter.set_current_consume("kinesis", "", stream, "")
    except Exception:
        log.debug("pipelines: failed to record kinesis consume side", exc_info=True)


if config._data_streams_enabled:
    core.on("botocore.kinesis.update_record", handle_kinesis_produce)
    core.on("botocore.eventbridge.update_messages", handle_eventbridge_produce)
    core.on("botocore.sqs_sns.update_messages", handle_sqs_sns_produce)
    core.on("botocore.sqs.ReceiveMessage.pre", handle_sqs_prepare)
    core.on("botocore.sqs.ReceiveMessage.post", handle_sqs_receive)
    core.on("botocore.kinesis.GetRecords.post", handle_kinesis_receive)

# Automatic DSM pipelines (DSM2-531): subscribed unconditionally — each handler
# returns immediately when pipelines reporting is disabled, so the feature is a
# no-op then. This module is imported whenever pipelines reporting is enabled
# (import wiring owned by the tracer-core agent), regardless of
# DD_DATA_STREAMS_ENABLED.
core.on("botocore.sqs_sns.update_messages", handle_pipelines_sqs_sns_produce)
core.on("botocore.kinesis.update_record", handle_pipelines_kinesis_produce)
core.on("botocore.eventbridge.update_messages", handle_pipelines_eventbridge_produce)
core.on("botocore.patched_api_call.started", handle_pipelines_api_call_started)
core.on("botocore.sqs.ReceiveMessage.post", handle_pipelines_sqs_receive)
core.on("botocore.kinesis.GetRecords.post", handle_pipelines_kinesis_receive)
