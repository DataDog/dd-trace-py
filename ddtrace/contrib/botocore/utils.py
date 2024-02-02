"""
Trace queries monitoring to aws api done via botocore client
"""
import base64
import json
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Tuple  # noqa:F401

from ddtrace import Span  # noqa:F401
from ddtrace import config

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanKind
from ...ext import aws
from ...ext import http
from ...internal.constants import COMPONENT
from ...internal.logger import get_logger
from ...internal.utils.formats import deep_getattr
from ...propagation.http import HTTPPropagator


log = get_logger(__name__)

MAX_EVENTBRIDGE_DETAIL_SIZE = 1 << 18  # 256KB

LINE_BREAK = "\n"


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
    If it's none of these, then we leave the message as it is.
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
        log.debug("Unable to parse payload, unable to inject trace context.")

    return None, None


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


def set_patched_api_call_span_tags(span, instance, args, params, endpoint_name, operation):
    span.set_tag_str(COMPONENT, config.botocore.integration_name)
    # set span.kind to the type of request being performed
    span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span.set_tag(SPAN_MEASURED_KEY)

    if args:
        # DEV: join is the fastest way of concatenating strings that is compatible
        # across Python versions (see
        # https://stackoverflow.com/questions/1316887/what-is-the-most-efficient-string-concatenation-method-in-python)
        span.resource = ".".join((endpoint_name, operation.lower()))
        span.set_tag("aws_service", endpoint_name)

        if params and not config.botocore["tag_no_params"]:
            aws._add_api_param_span_tags(span, endpoint_name, params)

    else:
        span.resource = endpoint_name

    region_name = deep_getattr(instance, "meta.region_name")

    span.set_tag_str("aws.agent", "botocore")
    if operation is not None:
        span.set_tag_str("aws.operation", operation)
    if region_name is not None:
        span.set_tag_str("aws.region", region_name)
        span.set_tag_str("region", region_name)

    # set analytics sample rate
    span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.botocore.get_analytics_sample_rate())


def set_response_metadata_tags(span, result):
    # type: (Span, Dict[str, Any]) -> None
    if not result or not result.get("ResponseMetadata"):
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


def extract_DD_context(messages):
    ctx = None
    if len(messages) >= 1:
        message = messages[0]
        context_json = extract_trace_context_json(message)
        if context_json is not None:
            child_of = HTTPPropagator.extract(context_json)
            if child_of.trace_id is not None:
                ctx = child_of
    return ctx


def extract_trace_context_json(message):
    context_json = None
    try:
        if message and message.get("Type") == "Notification":
            # This is potentially a DSM SNS notification
            if (
                "MessageAttributes" in message
                and "_datadog" in message["MessageAttributes"]
                and message["MessageAttributes"]["_datadog"]["Type"] == "Binary"
            ):
                context_json = json.loads(base64.b64decode(message["MessageAttributes"]["_datadog"]["Value"]).decode())
        elif (
            "MessageAttributes" in message
            and "_datadog" in message["MessageAttributes"]
            and "StringValue" in message["MessageAttributes"]["_datadog"]
        ):
            # The message originated from SQS
            context_json = json.loads(message["MessageAttributes"]["_datadog"]["StringValue"])
        elif (
            "MessageAttributes" in message
            and "_datadog" in message["MessageAttributes"]
            and "BinaryValue" in message["MessageAttributes"]["_datadog"]
        ):
            # Raw message delivery
            context_json = json.loads(message["MessageAttributes"]["_datadog"]["BinaryValue"].decode())
        # this is a kinesis message
        elif "Data" in message:
            # Raw message delivery
            _, data = get_kinesis_data_object(message["Data"])
            if "_datadog" in data:
                context_json = data["_datadog"]

        if context_json is None:
            # AWS SNS holds attributes within message body
            if "Body" in message:
                try:
                    body = json.loads(message["Body"])
                    return extract_trace_context_json(body)
                except ValueError:
                    log.debug("Unable to parse AWS message body.")
    except Exception:
        log.debug("Unable to parse AWS message attributes for Datadog Context.")
    return context_json
