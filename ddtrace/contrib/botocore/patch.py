"""
Trace queries and data streams monitoring to aws api done via botocore client
"""
import base64
import collections
from datetime import datetime
import json
import os
import typing
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union

import botocore.client
import botocore.exceptions

from ddtrace import config
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.settings.config import Config
from ddtrace.vendor import debtcollector
from ddtrace.vendor import wrapt

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanKind
from ...ext import SpanTypes
from ...ext import aws
from ...ext import http
from ...internal.compat import parse
from ...internal.constants import COMPONENT
from ...internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ...internal.logger import get_logger
from ...internal.schema import schematize_cloud_api_operation
from ...internal.schema import schematize_cloud_faas_operation
from ...internal.schema import schematize_cloud_messaging_operation
from ...internal.schema import schematize_service_name
from ...internal.utils import get_argument_value
from ...internal.utils.formats import asbool
from ...internal.utils.formats import deep_getattr
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from ..trace_utils import unwrap


_PATCHED_SUBMODULES = set()  # type: Set[str]

if typing.TYPE_CHECKING:  # pragma: no cover
    from ddtrace import Span

# Original botocore client class
_Botocore_client = botocore.client.BaseClient

ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = {"params", "path", "verb"}

MAX_KINESIS_DATA_SIZE = 1 << 20  # 1MB
MAX_EVENTBRIDGE_DETAIL_SIZE = 1 << 18  # 256KB

LINE_BREAK = "\n"

log = get_logger(__name__)

if os.getenv("DD_AWS_TAG_ALL_PARAMS") is not None:
    debtcollector.deprecate(
        "Using environment variable 'DD_AWS_TAG_ALL_PARAMS' is deprecated",
        message="The botocore integration no longer includes all API parameters by default.",
        removal_version="2.0.0",
    )

# Botocore default settings
config._add(
    "botocore",
    {
        "distributed_tracing": asbool(os.getenv("DD_BOTOCORE_DISTRIBUTED_TRACING", default=True)),
        "invoke_with_legacy_context": asbool(os.getenv("DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT", default=False)),
        "operations": collections.defaultdict(Config._HTTPServerConfig),
        "tag_no_params": asbool(os.getenv("DD_AWS_TAG_NO_PARAMS", default=False)),
        "tag_all_params": asbool(os.getenv("DD_AWS_TAG_ALL_PARAMS", default=False)),
        "instrument_internals": asbool(os.getenv("DD_BOTOCORE_INSTRUMENT_INTERNALS", default=False)),
    },
)


class TraceInjectionSizeExceed(Exception):
    pass


class TraceInjectionDecodingError(Exception):
    pass


def inject_trace_data_to_message_attributes(trace_data, entry, endpoint_service=None):
    # type: (Dict[str, str], Dict[str, Any], Optional[str]) -> None
    """
    :trace_data: trace headers and DSM pathway to be stored in the entry's MessageAttributes
    :entry: an SQS or SNS record
    :endpoint_service: endpoint of message, "sqs" or "sns"

    Inject trace headers and DSM info into the SQS or SNS record's MessageAttributes
    """
    if "MessageAttributes" not in entry:
        entry["MessageAttributes"] = {}
    # Max of 10 message attributes.
    if len(entry["MessageAttributes"]) < 10:
        if endpoint_service == "sqs":
            # Use String since changing this to Binary would be a breaking
            # change as other tracers expect this to be a String.
            entry["MessageAttributes"]["_datadog"] = {"DataType": "String", "StringValue": json.dumps(trace_data)}
        elif endpoint_service == "sns":
            # Use Binary since SNS subscription filter policies fail silently
            # with JSON strings https://github.com/DataDog/datadog-lambda-js/pull/269
            # AWS will encode our value if it sees "Binary"
            entry["MessageAttributes"]["_datadog"] = {"DataType": "Binary", "BinaryValue": json.dumps(trace_data)}
        else:
            log.warning("skipping trace injection, endpoint is not SNS or SQS")
    else:
        # In the event a record has 10 or more msg attributes we cannot add our _datadog msg attribute
        log.warning("skipping trace injection, max number (10) of MessageAttributes exceeded")


def get_topic_arn(params):
    # type: (str) -> str
    """
    :params: contains the params for the current botocore action

    Return the name of the topic given the params
    """
    sns_arn = params["TopicArn"]
    return sns_arn


def get_queue_name(params):
    # type: (str) -> str
    """
    :params: contains the params for the current botocore action

    Return the name of the queue given the params
    """
    queue_url = params["QueueUrl"]
    url = parse.urlparse(queue_url)
    return url.path.rsplit("/", 1)[-1]


def get_stream_arn(params):
    # type: (str) -> str
    """
    :params: contains the params for the current botocore action

    Return the name of the stream given the params
    """
    stream_arn = params.get("StreamARN", "")
    return stream_arn


def get_pathway(pin, endpoint_service, dsm_identifier):
    # type: (Pin, str, str) -> str
    """
    :pin: patch info for the botocore client
    :endpoint_service: the name  of the service (i.e. 'sns', 'sqs', 'kinesis')
    :dsm_identifier: the identifier for the topic/queue/stream/etc

    Set the data streams monitoring checkpoint and return the encoded pathway
    """
    path_type = "type:{}".format(endpoint_service)
    if not dsm_identifier:
        log.debug("pathway being generated with unrecognized service: ", dsm_identifier)

    pathway = pin.tracer.data_streams_processor.set_checkpoint(
        ["direction:out", "topic:{}".format(dsm_identifier), path_type]
    )
    return pathway.encode_b64()


def inject_trace_to_sqs_or_sns_batch_message(params, span, endpoint_service=None, pin=None, data_streams_enabled=False):
    # type: (Any, Span, Optional[str], Optional[Pin], Optional[bool]) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated
    :endpoint_service: endpoint of message, "sqs" or "sns"
    :pin: patch info for the botocore client
    :data_streams_enabled: boolean for whether data streams monitoring is enabled

    Inject trace headers and DSM info into MessageAttributes for all SQS or SNS records inside a batch
    """

    trace_data = {}
    HTTPPropagator.inject(span.context, trace_data)

    # An entry here is an SNS or SQS record, and depending on how it was published,
    # it could either show up under Entries (in case of PutRecords),
    # or PublishBatchRequestEntries (in case of PublishBatch).
    entries = params.get("Entries", params.get("PublishBatchRequestEntries", []))
    for entry in entries:
        if data_streams_enabled:
            dsm_identifier = None
            if endpoint_service == "sqs":
                dsm_identifier = get_queue_name(params)
            elif endpoint_service == "sns":
                dsm_identifier = get_topic_arn(params)
            trace_data[PROPAGATION_KEY_BASE_64] = get_pathway(pin, endpoint_service, dsm_identifier)
        inject_trace_data_to_message_attributes(trace_data, entry, endpoint_service)


def inject_trace_to_sqs_or_sns_message(params, span, endpoint_service=None, pin=None, data_streams_enabled=False):
    # type: (Any, Span, Optional[str], Optional[Pin], Optional[bool]) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated
    :endpoint_service: endpoint of message, "sqs" or "sns"
    :pin: patch info for the botocore client
    :data_streams_enabled: boolean for whether data streams monitoring is enabled

    Inject trace headers and DSM info into MessageAttributes for the SQS or SNS record
    """
    trace_data = {}
    HTTPPropagator.inject(span.context, trace_data)

    if data_streams_enabled:
        dsm_identifier = None
        if endpoint_service == "sqs":
            dsm_identifier = get_queue_name(params)
        elif endpoint_service == "sns":
            dsm_identifier = get_topic_arn(params)

        trace_data[PROPAGATION_KEY_BASE_64] = get_pathway(pin, endpoint_service, dsm_identifier)

    inject_trace_data_to_message_attributes(trace_data, params, endpoint_service)


def inject_trace_to_eventbridge_detail(params, span):
    # type: (Any, Span) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated

    Inject trace headers into the EventBridge record if the record's Detail object contains a JSON string
    Max size per event is 256KB (https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-putevent-size.html)
    """
    if "Entries" not in params:
        log.warning("Unable to inject context. The Event Bridge event had no Entries.")
        return

    for entry in params["Entries"]:
        detail = {}
        if "Detail" in entry:
            try:
                detail = json.loads(entry["Detail"])
            except ValueError:
                log.warning("Detail is not a valid JSON string")
                continue

        detail["_datadog"] = {}
        HTTPPropagator.inject(span.context, detail["_datadog"])
        detail_json = json.dumps(detail)

        # check if detail size will exceed max size with headers
        detail_size = len(detail_json)
        if detail_size >= MAX_EVENTBRIDGE_DETAIL_SIZE:
            log.warning("Detail with trace injection (%s) exceeds limit (%s)", detail_size, MAX_EVENTBRIDGE_DETAIL_SIZE)
            continue

        entry["Detail"] = detail_json


def get_json_from_str(data_str):
    # type: (str) -> Tuple[str, Optional[Dict[str, Any]]]
    data_obj = json.loads(data_str)

    if data_str.endswith(LINE_BREAK):
        return LINE_BREAK, data_obj
    return None, data_obj


def get_kinesis_data_object(data):
    # type: (str) -> Tuple[str, Optional[Dict[str, Any]]]
    """
    :data: the data from a kinesis stream

    The data from a kinesis stream comes as a string (could be json, base64 encoded, etc.)
    We support injecting our trace context in the following three cases:
    - json string
    - byte encoded json string
    - base64 encoded json string
    If it's neither of these, then we leave the message as it is.
    """

    # check if data is a json string
    try:
        return get_json_from_str(data)
    except Exception:
        log.debug("Kinesis data is not a JSON string. Trying Byte encoded JSON string.")

    # check if data is an encoded json string
    try:
        data_str = data.decode("ascii")
        return get_json_from_str(data_str)
    except Exception:
        log.debug("Kinesis data is not a JSON string encoded. Trying Base64 encoded JSON string.")

    # check if data is a base64 encoded json string
    try:
        data_str = base64.b64decode(data).decode("ascii")
        return get_json_from_str(data_str)
    except Exception:
        log.error("Unable to parse payload, unable to inject trace context.")

    return None, None


def inject_trace_to_kinesis_stream_data(record, span):
    # type: (Dict[str, Any], Span) -> None
    """
    :record: contains args for the current botocore action, Kinesis record is at index 1
    :span: the span which provides the trace context to be propagated

    Inject trace headers into the Kinesis record's Data field in addition to the existing
    data. Only possible if the existing data is JSON string or base64 encoded JSON string
    Max data size per record is 1MB (https://aws.amazon.com/kinesis/data-streams/faqs/)
    """
    if "Data" not in record:
        log.warning("Unable to inject context. The kinesis stream has no data")
        return

    data = record["Data"]
    line_break, data_obj = get_kinesis_data_object(data)
    if data_obj is not None:
        data_obj["_datadog"] = {}
        HTTPPropagator.inject(span.context, data_obj["_datadog"])
        data_json = json.dumps(data_obj)

        # if original string had a line break, add it back
        if line_break is not None:
            data_json += line_break

        # check if data size will exceed max size with headers
        data_size = len(data_json)
        if data_size >= MAX_KINESIS_DATA_SIZE:
            raise TraceInjectionSizeExceed(
                "Data including trace injection ({}) exceeds ({})".format(data_size, MAX_KINESIS_DATA_SIZE)
            )

        record["Data"] = data_json


def record_data_streams_path_for_kinesis_stream(pin, params, results):
    stream_arn = params.get("StreamARN")

    if not stream_arn:
        log.debug("Unable to determine StreamARN for request with params: ", params)
        return

    processor = pin.tracer.data_streams_processor
    pathway = processor.new_pathway()
    for record in results.get("Records", []):
        time_estimate = record.get("ApproximateArrivalTimestamp", datetime.now()).timestamp()
        pathway.set_checkpoint(
            ["direction:in", "topic:" + stream_arn, "type:kinesis"],
            edge_start_sec_override=time_estimate,
            pathway_start_sec_override=time_estimate,
        )


def inject_trace_to_kinesis_stream(params, span, pin=None, data_streams_enabled=False):
    # type: (List[Any], Span, Optional[Pin], Optional[bool]) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated
    :pin: patch info for the botocore client
    :data_streams_enabled: boolean for whether data streams monitoring is enabled

    Max data size per record is 1MB (https://aws.amazon.com/kinesis/data-streams/faqs/)

    """
    if data_streams_enabled:
        stream_arn = get_stream_arn(params)
        if stream_arn:  # If stream ARN isn't specified, we give up (it is not a required param)

            # put_records has a "Records" entry but put_record does not, so we fake a record to
            # collapse the logic for the two cases
            for _ in params.get("Records", ["fake_record"]):
                # In other DSM code, you'll see the pathway + context injection but not here.
                # Kinesis DSM doesn't inject any data, so we only need to generate checkpoints.
                get_pathway(pin, "kinesis", stream_arn)

    if "Records" in params:
        records = params["Records"]

        if records:
            record = records[0]
            inject_trace_to_kinesis_stream_data(record, span)
    elif "Data" in params:
        inject_trace_to_kinesis_stream_data(params, span)


def modify_client_context(client_context_object, trace_headers):
    if config.botocore["invoke_with_legacy_context"]:
        trace_headers = {"_datadog": trace_headers}

    if "custom" in client_context_object:
        client_context_object["custom"].update(trace_headers)
    else:
        client_context_object["custom"] = trace_headers


def inject_trace_to_client_context(params, span):
    trace_headers = {}
    HTTPPropagator.inject(span.context, trace_headers)
    client_context_object = {}
    if "ClientContext" in params:
        try:
            client_context_json = base64.b64decode(params["ClientContext"]).decode("utf-8")
            client_context_object = json.loads(client_context_json)
        except Exception:
            log.warning("malformed client_context=%s", params["ClientContext"], exc_info=True)
            return
    modify_client_context(client_context_object, trace_headers)
    try:
        json_context = json.dumps(client_context_object).encode("utf-8")
    except Exception:
        log.warning("unable to encode modified client context as json: %s", client_context_object, exc_info=True)
        return
    params["ClientContext"] = base64.b64encode(json_context).decode("utf-8")


def patch():
    if getattr(botocore.client, "_datadog_patch", False):
        return
    setattr(botocore.client, "_datadog_patch", True)

    wrapt.wrap_function_wrapper("botocore.client", "BaseClient._make_api_call", patched_api_call)
    Pin(service="aws").onto(botocore.client.BaseClient)
    wrapt.wrap_function_wrapper("botocore.parsers", "ResponseParser.parse", patched_lib_fn)
    Pin(service="aws").onto(botocore.parsers.ResponseParser)
    _PATCHED_SUBMODULES.clear()


def unpatch():
    _PATCHED_SUBMODULES.clear()
    if getattr(botocore.client, "_datadog_patch", False):
        setattr(botocore.client, "_datadog_patch", False)
        unwrap(botocore.parsers.ResponseParser, "parse")
        unwrap(botocore.client.BaseClient, "_make_api_call")


def patch_submodules(submodules):
    # type: (Union[List[str], bool]) -> None
    if isinstance(submodules, bool) and submodules:
        _PATCHED_SUBMODULES.clear()
    elif isinstance(submodules, list):
        submodules = [sub_module.lower() for sub_module in submodules]
        _PATCHED_SUBMODULES.update(submodules)


def patched_lib_fn(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled() or not config.botocore["instrument_internals"]:
        return original_func(*args, **kwargs)
    with pin.tracer.trace("{}.{}".format(original_func.__module__, original_func.__name__)) as span:
        span.set_tag_str(COMPONENT, config.botocore.integration_name)
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        return original_func(*args, **kwargs)


def patched_api_call(original_func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = deep_getattr(instance, "_endpoint._endpoint_prefix")

    if _PATCHED_SUBMODULES and endpoint_name not in _PATCHED_SUBMODULES:
        return original_func(*args, **kwargs)

    trace_operation = schematize_cloud_api_operation(
        "{}.command".format(endpoint_name), cloud_provider="aws", cloud_service=endpoint_name
    )
    with pin.tracer.trace(
        trace_operation,
        service=schematize_service_name("{}.{}".format(pin.service, endpoint_name)),
        span_type=SpanTypes.HTTP,
    ) as span:
        span.set_tag_str(COMPONENT, config.botocore.integration_name)

        # set span.kind to the type of request being performed
        span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

        span.set_tag(SPAN_MEASURED_KEY)
        operation = None
        if args:
            operation = get_argument_value(args, kwargs, 0, "operation_name")
            params = get_argument_value(args, kwargs, 1, "api_params")
            # DEV: join is the fastest way of concatenating strings that is compatible
            # across Python versions (see
            # https://stackoverflow.com/questions/1316887/what-is-the-most-efficient-string-concatenation-method-in-python)
            span.resource = ".".join((endpoint_name, operation.lower()))
            span.set_tag("aws_service", endpoint_name)
            if config.botocore["distributed_tracing"]:
                try:
                    if endpoint_name == "lambda" and operation == "Invoke":
                        inject_trace_to_client_context(params, span)
                        span.name = schematize_cloud_faas_operation(
                            trace_operation, cloud_provider="aws", cloud_service="lambda"
                        )
                    if endpoint_name == "sqs" and operation == "SendMessage":
                        inject_trace_to_sqs_or_sns_message(
                            params,
                            span,
                            endpoint_service=endpoint_name,
                            pin=pin,
                            data_streams_enabled=config._data_streams_enabled,
                        )
                        span.name = schematize_cloud_messaging_operation(
                            trace_operation, cloud_provider="aws", cloud_service="sqs", direction=SpanDirection.OUTBOUND
                        )
                    if endpoint_name == "sqs" and operation == "SendMessageBatch":
                        inject_trace_to_sqs_or_sns_batch_message(
                            params,
                            span,
                            endpoint_service=endpoint_name,
                            pin=pin,
                            data_streams_enabled=config._data_streams_enabled,
                        )
                        span.name = schematize_cloud_messaging_operation(
                            trace_operation, cloud_provider="aws", cloud_service="sqs", direction=SpanDirection.OUTBOUND
                        )
                    if endpoint_name == "sqs" and operation == "ReceiveMessage":
                        span.name = schematize_cloud_messaging_operation(
                            trace_operation, cloud_provider="aws", cloud_service="sqs", direction=SpanDirection.INBOUND
                        )
                    if endpoint_name == "events" and operation == "PutEvents":
                        inject_trace_to_eventbridge_detail(params, span)
                        span.name = schematize_cloud_messaging_operation(
                            trace_operation,
                            cloud_provider="aws",
                            cloud_service="events",
                            direction=SpanDirection.OUTBOUND,
                        )
                    if endpoint_name == "kinesis" and operation in {"PutRecord", "PutRecords"}:
                        inject_trace_to_kinesis_stream(
                            params, span, pin=pin, data_streams_enabled=config._data_streams_enabled
                        )
                        span.name = schematize_cloud_messaging_operation(
                            trace_operation,
                            cloud_provider="aws",
                            cloud_service="kinesis",
                            direction=SpanDirection.OUTBOUND,
                        )
                    if endpoint_name == "sns" and operation == "Publish":
                        inject_trace_to_sqs_or_sns_message(
                            params,
                            span,
                            endpoint_service=endpoint_name,
                            pin=pin,
                            data_streams_enabled=config._data_streams_enabled,
                        )
                        span.name = schematize_cloud_messaging_operation(
                            trace_operation, cloud_provider="aws", cloud_service="sns", direction=SpanDirection.OUTBOUND
                        )
                    if endpoint_name == "sns" and operation == "PublishBatch":
                        inject_trace_to_sqs_or_sns_batch_message(
                            params,
                            span,
                            endpoint_service=endpoint_name,
                            pin=pin,
                            data_streams_enabled=config._data_streams_enabled,
                        )
                        span.name = schematize_cloud_messaging_operation(
                            trace_operation, cloud_provider="aws", cloud_service="sns", direction=SpanDirection.OUTBOUND
                        )
                except Exception:
                    log.warning("Unable to inject trace context", exc_info=True)

            if params and not config.botocore["tag_no_params"]:
                aws._add_api_param_span_tags(span, endpoint_name, params)

        else:
            span.resource = endpoint_name

        if not config.botocore["tag_no_params"] and config.botocore["tag_all_params"]:
            aws.add_span_arg_tags(span, endpoint_name, args, ARGS_NAME, TRACED_ARGS)

        region_name = deep_getattr(instance, "meta.region_name")

        span.set_tag_str("aws.agent", "botocore")
        if operation is not None:
            span.set_tag_str("aws.operation", operation)
        if region_name is not None:
            span.set_tag_str("aws.region", region_name)
            span.set_tag_str("region", region_name)

        # set analytics sample rate
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.botocore.get_analytics_sample_rate())

        try:
            if endpoint_name == "sqs" and operation == "ReceiveMessage" and config._data_streams_enabled:
                queue_name = get_queue_name(params)

                if "MessageAttributeNames" not in params:
                    params.update({"MessageAttributeNames": ["_datadog"]})
                elif "_datadog" not in params["MessageAttributeNames"]:
                    params.update({"MessageAttributeNames": list(params["MessageAttributeNames"]) + ["_datadog"]})

                result = original_func(*args, **kwargs)
                _set_response_metadata_tags(span, result)

                try:
                    if "Messages" in result:
                        for message in result["Messages"]:
                            message_body = None
                            context_json = None

                            if "Body" in message:
                                try:
                                    message_body = json.loads(message["Body"])
                                except ValueError:
                                    log.debug("Unable to parse message body, treat as non-json")

                            if message_body and message_body.get("Type") == "Notification":
                                # This is potentially a DSM SNS notification
                                if (
                                    "MessageAttributes" in message_body
                                    and "_datadog" in message_body["MessageAttributes"]
                                    and message_body["MessageAttributes"]["_datadog"]["Type"] == "Binary"
                                ):
                                    context_json = json.loads(
                                        base64.b64decode(
                                            message_body["MessageAttributes"]["_datadog"]["Value"]
                                        ).decode()
                                    )
                            elif (
                                "MessageAttributes" in message
                                and "_datadog" in message["MessageAttributes"]
                                and "StringValue" in message["MessageAttributes"]["_datadog"]
                            ):
                                # The message originated from SQS
                                message_body = message
                                context_json = json.loads(message_body["MessageAttributes"]["_datadog"]["StringValue"])

                            pathway = context_json.get(PROPAGATION_KEY_BASE_64, None) if context_json else None
                            ctx = pin.tracer.data_streams_processor.decode_pathway_b64(pathway)
                            ctx.set_checkpoint(["direction:in", "topic:" + queue_name, "type:sqs"])

                except Exception:
                    log.debug("Error receiving SQS message with data streams monitoring enabled", exc_info=True)

                return result

            else:
                result = original_func(*args, **kwargs)

                if endpoint_name == "kinesis" and operation == "GetRecords" and config._data_streams_enabled:
                    try:
                        record_data_streams_path_for_kinesis_stream(pin, params, result)
                    except Exception:
                        log.debug("Failed to report data streams monitoring info for kinesis", exc_info=True)

                _set_response_metadata_tags(span, result)
                return result

        except botocore.exceptions.ClientError as e:
            # `ClientError.response` contains the result, so we can still grab response metadata
            _set_response_metadata_tags(span, e.response)

            # If we have a status code, and the status code is not an error,
            #   then ignore the exception being raised
            status_code = span.get_tag(http.STATUS_CODE)
            if status_code and not config.botocore.operations[span.resource].is_error_code(int(status_code)):
                span._ignore_exception(botocore.exceptions.ClientError)
            raise


def _set_response_metadata_tags(span, result):
    # type: (Span, Dict[str, Any]) -> None
    if not result.get("ResponseMetadata"):
        return
    response_meta = result["ResponseMetadata"]

    if "HTTPStatusCode" in response_meta:
        status_code = response_meta["HTTPStatusCode"]
        span.set_tag(http.STATUS_CODE, status_code)

        # Mark this span as an error if requested
        if config.botocore.operations[span.resource].is_error_code(int(status_code)):
            span.error = 1

    if "RetryAttempts" in response_meta:
        span.set_tag("retry_attempts", response_meta["RetryAttempts"])

    if "RequestId" in response_meta:
        span.set_tag_str("aws.requestid", response_meta["RequestId"])
