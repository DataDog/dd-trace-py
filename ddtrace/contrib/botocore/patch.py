"""
Trace queries to aws api done via botocore client
"""
import base64
import json
import os
import typing
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import botocore.client

from ddtrace import config
from ddtrace.vendor import wrapt

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import aws
from ...ext import http
from ...internal.logger import get_logger
from ...internal.utils import get_argument_value
from ...internal.utils.formats import asbool
from ...internal.utils.formats import deep_getattr
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from ..trace_utils import unwrap


if typing.TYPE_CHECKING:
    from ddtrace import Span

# Original botocore client class
_Botocore_client = botocore.client.BaseClient

ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = {"params", "path", "verb"}

MAX_KINESIS_DATA_SIZE = 1 << 20  # 1MB
MAX_EVENTBRIDGE_DETAIL_SIZE = 1 << 18  # 256KB

log = get_logger(__name__)

# Botocore default settings
config._add(
    "botocore",
    {
        "distributed_tracing": asbool(os.getenv("DD_BOTOCORE_DISTRIBUTED_TRACING", default=True)),
        "invoke_with_legacy_context": asbool(os.getenv("DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT", default=False)),
    },
)


class TraceInjectionSizeExceed(Exception):
    pass


class TraceInjectionDecodingError(Exception):
    pass


def inject_trace_data_to_message_attributes(trace_data, entry):
    # type: (Dict[str, str], Dict[str, Any]) -> None
    """
    :trace_data: trace headers to be stored in the entry's MessageAttributes
    :entry: an SQS or SNS record

    Inject trace headers into the an SQS or SNS record's MessageAttributes
    """
    if "MessageAttributes" not in entry:
        entry["MessageAttributes"] = {}
    # An Amazon SQS message can contain up to 10 metadata attributes.
    if len(entry["MessageAttributes"]) < 10:
        entry["MessageAttributes"]["_datadog"] = {"DataType": "String", "StringValue": json.dumps(trace_data)}
    else:
        log.warning("skipping trace injection, max number (10) of MessageAttributes exceeded")


def inject_trace_to_sqs_or_sns_batch_message(params, span):
    # type: (Any, Span) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated

    Inject trace headers into MessageAttributes for all SQS or SNS records inside a batch
    """
    trace_data = {}
    HTTPPropagator.inject(span.context, trace_data)

    # An entry here is an SNS or SQS record, and depending on how it was published,
    # it could either show up under Entries (in case of PutRecords),
    # or PublishBatchRequestEntries (in case of PublishBatch).
    entries = params.get("Entries", params.get("PublishBatchRequestEntries", []))
    for entry in entries:
        inject_trace_data_to_message_attributes(trace_data, entry)


def inject_trace_to_sqs_or_sns_message(params, span):
    # type: (Any, Span) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated

    Inject trace headers into MessageAttributes for the SQS or SNS record
    """
    trace_data = {}
    HTTPPropagator.inject(span.context, trace_data)

    inject_trace_data_to_message_attributes(trace_data, params)


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


def get_kinesis_data_object(data):
    # type: (str, Optional[bool]) -> Optional[Dict[str, Any]]
    """
    :data: the data from a kinesis stream
    :try_b64: whether we should try to decode the string as base64

    The data from a kinesis stream comes as a string (could be json, base64 encoded, etc.)
    We support injecting our trace context in the following two cases:
    - json string
    - base64 encoded json string
    If it's neither of these, then we leave the message as it is.
    """

    # check if data is a json string
    try:
        return json.loads(data)
    except ValueError:
        pass

    # check if data is a base64 encoded json string
    try:
        return json.loads(base64.b64decode(data).decode("ascii"))
    except ValueError:
        raise TraceInjectionDecodingError("Unable to parse kinesis streams data string")


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
    data_obj = get_kinesis_data_object(data)
    data_obj["_datadog"] = {}
    HTTPPropagator.inject(span.context, data_obj["_datadog"])
    data_json = json.dumps(data_obj)

    # check if data size will exceed max size with headers
    data_size = len(data_json)
    if data_size >= MAX_KINESIS_DATA_SIZE:
        raise TraceInjectionSizeExceed(
            "Data including trace injection ({}) exceeds ({})".format(data_size, MAX_KINESIS_DATA_SIZE)
        )

    record["Data"] = data_json


def inject_trace_to_kinesis_stream(params, span):
    # type: (List[Any], Span) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated

    Inject trace headers into the Kinesis batch's first record's Data field.
    Only possible if the existing data is JSON string or base64 encoded JSON string
    Max data size per record is 1MB (https://aws.amazon.com/kinesis/data-streams/faqs/)
    """
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


def unpatch():
    if getattr(botocore.client, "_datadog_patch", False):
        setattr(botocore.client, "_datadog_patch", False)
        unwrap(botocore.client.BaseClient, "_make_api_call")


def patched_api_call(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = deep_getattr(instance, "_endpoint._endpoint_prefix")

    with pin.tracer.trace(
        "{}.command".format(endpoint_name), service="{}.{}".format(pin.service, endpoint_name), span_type=SpanTypes.HTTP
    ) as span:
        span.set_tag(SPAN_MEASURED_KEY)
        operation = None
        if args:
            operation = get_argument_value(args, kwargs, 0, "operation_name")
            params = get_argument_value(args, kwargs, 1, "api_params")
            # DEV: join is the fastest way of concatenating strings that is compatible
            # across Python versions (see
            # https://stackoverflow.com/questions/1316887/what-is-the-most-efficient-string-concatenation-method-in-python)
            span.resource = ".".join((endpoint_name, operation.lower()))

            if config.botocore["distributed_tracing"]:
                try:
                    if endpoint_name == "lambda" and operation == "Invoke":
                        inject_trace_to_client_context(params, span)
                    if endpoint_name == "sqs" and operation == "SendMessage":
                        inject_trace_to_sqs_or_sns_message(params, span)
                    if endpoint_name == "sqs" and operation == "SendMessageBatch":
                        inject_trace_to_sqs_or_sns_batch_message(params, span)
                    if endpoint_name == "events" and operation == "PutEvents":
                        inject_trace_to_eventbridge_detail(params, span)
                    if endpoint_name == "kinesis" and (operation == "PutRecord" or operation == "PutRecords"):
                        inject_trace_to_kinesis_stream(params, span)
                    if endpoint_name == "sns" and operation == "Publish":
                        inject_trace_to_sqs_or_sns_message(params, span)
                    if endpoint_name == "sns" and operation == "PublishBatch":
                        inject_trace_to_sqs_or_sns_batch_message(params, span)
                except Exception:
                    log.warning("Unable to inject trace context", exc_info=True)

        else:
            span.resource = endpoint_name

        aws.add_span_arg_tags(span, endpoint_name, args, ARGS_NAME, TRACED_ARGS)

        region_name = deep_getattr(instance, "meta.region_name")

        span._set_str_tag("aws.agent", "botocore")
        if operation is not None:
            span._set_str_tag("aws.operation", operation)
        if region_name is not None:
            span._set_str_tag("aws.region", region_name)

        result = original_func(*args, **kwargs)

        response_meta = result.get("ResponseMetadata")
        if response_meta:
            if "HTTPStatusCode" in response_meta:
                span.set_tag(http.STATUS_CODE, response_meta["HTTPStatusCode"])

            if "RetryAttempts" in response_meta:
                span.set_tag("retry_attempts", response_meta["RetryAttempts"])

            if "RequestId" in response_meta:
                span.set_tag("aws.requestid", response_meta["RequestId"])

        # set analytics sample rate
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.botocore.get_analytics_sample_rate())

        return result
