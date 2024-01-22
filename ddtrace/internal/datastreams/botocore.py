import base64
from datetime import datetime
import json

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.compat import parse
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def get_pathway(endpoint_service, dsm_identifier):
    # type: (str, str) -> str
    """
    :endpoint_service: the name  of the service (i.e. 'sns', 'sqs', 'kinesis')
    :dsm_identifier: the identifier for the topic/queue/stream/etc

    Set the data streams monitoring checkpoint and return the encoded pathway
    """
    from . import data_streams_processor as processor

    path_type = "type:{}".format(endpoint_service)
    if not dsm_identifier:
        log.debug("pathway being generated with unrecognized service: ", dsm_identifier)

    pathway = processor().set_checkpoint(["direction:out", "topic:{}".format(dsm_identifier), path_type])
    return pathway.encode_b64()


def get_queue_name(params):
    # type: (dict) -> str
    """
    :params: contains the params for the current botocore action

    Return the name of the queue given the params
    """
    queue_url = params["QueueUrl"]
    url = parse.urlparse(queue_url)
    return url.path.rsplit("/", 1)[-1]


def get_topic_arn(params):
    # type: (dict) -> str
    """
    :params: contains the params for the current botocore action

    Return the name of the topic given the params
    """
    sns_arn = params["TopicArn"]
    return sns_arn


def get_stream_arn(params):
    # type: (dict) -> str
    """
    :params: contains the params for the current botocore action

    Return the name of the stream given the params
    """
    stream_arn = params.get("StreamARN", "")
    return stream_arn


def inject_context(trace_data, endpoint_service, dsm_identifier):
    pathway = get_pathway(endpoint_service, dsm_identifier)

    if trace_data is not None:
        trace_data[PROPAGATION_KEY_BASE_64] = pathway


def handle_kinesis_produce(params):
    stream_arn = get_stream_arn(params)
    if stream_arn:  # If stream ARN isn't specified, we give up (it is not a required param)
        # put_records has a "Records" entry but put_record does not, so we fake a record to
        # collapse the logic for the two cases
        for _ in params.get("Records", ["fake_record"]):
            # In other DSM code, you'll see the pathway + context injection but not here.
            # Kinesis DSM doesn't inject any data, so we only need to generate checkpoints.
            inject_context(None, "kinesis", stream_arn)


def handle_sqs_sns_produce(endpoint_service, trace_data, params):
    dsm_identifier = None
    if endpoint_service == "sqs":
        dsm_identifier = get_queue_name(params)
    elif endpoint_service == "sns":
        dsm_identifier = get_topic_arn(params)
    inject_context(trace_data, endpoint_service, dsm_identifier)


def handle_sqs_prepare(params):
    if "MessageAttributeNames" not in params:
        params.update({"MessageAttributeNames": ["_datadog"]})
    elif "_datadog" not in params["MessageAttributeNames"]:
        params.update({"MessageAttributeNames": list(params["MessageAttributeNames"]) + ["_datadog"]})


def get_datastreams_context(message):
    """
    Formats we're aware of:
        - message.Body.MessageAttributes._datadog.Value.decode() (SQS)
        - message.MessageAttributes._datadog.StringValue (SNS -> SQS)
        - message.MesssageAttributes._datadog.BinaryValue.decode() (SNS -> SQS, raw)
    """
    context_json = None
    message_body = message
    try:
        message_body = json.loads(message.get("Body"))
    except ValueError:
        log.debug("Unable to parse message body, treat as non-json")

    if "MessageAttributes" not in message_body:
        log.debug("DataStreams skipped message: %r", message)
        return None

    if "_datadog" not in message_body["MessageAttributes"]:
        log.debug("DataStreams skipped message: %r", message)
        return None

    if message_body.get("Type") == "Notification":
        # This is potentially a DSM SNS notification
        if message_body["MessageAttributes"]["_datadog"]["Type"] == "Binary":
            context_json = json.loads(base64.b64decode(message_body["MessageAttributes"]["_datadog"]["Value"]).decode())
    elif "StringValue" in message["MessageAttributes"]["_datadog"]:
        # The message originated from SQS
        context_json = json.loads(message_body["MessageAttributes"]["_datadog"]["StringValue"])
    elif "BinaryValue" in message["MessageAttributes"]["_datadog"]:
        # Raw message delivery
        context_json = json.loads(message_body["MessageAttributes"]["_datadog"]["BinaryValue"].decode())
    else:
        log.debug("DataStreams did not handle message: %r", message)

    return context_json


def handle_sqs_receive(params, result):
    from . import data_streams_processor as processor

    queue_name = get_queue_name(params)

    for message in result.get("Messages"):
        try:
            context_json = get_datastreams_context(message)
            pathway = context_json.get(PROPAGATION_KEY_BASE_64, None) if context_json else None
            ctx = processor().decode_pathway_b64(pathway)
            ctx.set_checkpoint(["direction:in", "topic:" + queue_name, "type:sqs"])

        except Exception:
            log.debug("Error receiving SQS message with data streams monitoring enabled", exc_info=True)


def record_data_streams_path_for_kinesis_stream(params, results):
    from . import data_streams_processor as processor

    stream_arn = params.get("StreamARN")

    if not stream_arn:
        log.debug("Unable to determine StreamARN for request with params: ", params)
        return

    pathway = processor().new_pathway()
    for record in results.get("Records", []):
        time_estimate = record.get("ApproximateArrivalTimestamp", datetime.now()).timestamp()
        pathway.set_checkpoint(
            ["direction:in", "topic:" + stream_arn, "type:kinesis"],
            edge_start_sec_override=time_estimate,
            pathway_start_sec_override=time_estimate,
        )


def handle_kinesis_receive(params, result):
    try:
        record_data_streams_path_for_kinesis_stream(params, result)
    except Exception:
        log.debug("Failed to report data streams monitoring info for kinesis", exc_info=True)


if config._data_streams_enabled:
    core.on("botocore.kinesis.start", handle_kinesis_produce)
    core.on("botocore.sqs_sns.start", handle_sqs_sns_produce)
    core.on("botocore.sqs.ReceiveMessage.pre", handle_sqs_prepare)
    core.on("botocore.sqs.ReceiveMessage.post", handle_sqs_receive)
    core.on("botocore.kinesis.GetRecords.post", handle_kinesis_receive)
