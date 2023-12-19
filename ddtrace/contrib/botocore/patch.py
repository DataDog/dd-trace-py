"""
Trace queries and data streams monitoring to aws api done via botocore client
"""
import base64
import collections
import json
import os
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Set  # noqa:F401
from typing import Tuple  # noqa:F401
from typing import Union  # noqa:F401

from botocore import __version__
import botocore.client
import botocore.exceptions

from ddtrace import Span  # noqa:F401
from ddtrace import config
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.settings.config import Config
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
from .services.kinesis import get_kinesis_data_object
from .services.kinesis import patched_kinesis_api_call
from .services.sqs import inject_trace_to_sqs_or_sns_batch_message
from .services.sqs import inject_trace_to_sqs_or_sns_message
from .services.sqs import patched_sqs_api_call


_PATCHED_SUBMODULES = set()  # type: Set[str]

# Original botocore client class
_Botocore_client = botocore.client.BaseClient

ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = {"params", "path", "verb"}

MAX_EVENTBRIDGE_DETAIL_SIZE = 1 << 18  # 256KB

LINE_BREAK = "\n"

log = get_logger(__name__)


# Botocore default settings
config._add(
    "botocore",
    {
        "distributed_tracing": asbool(os.getenv("DD_BOTOCORE_DISTRIBUTED_TRACING", default=True)),
        "invoke_with_legacy_context": asbool(os.getenv("DD_BOTOCORE_INVOKE_WITH_LEGACY_CONTEXT", default=False)),
        "operations": collections.defaultdict(Config._HTTPServerConfig),
        "tag_no_params": asbool(os.getenv("DD_AWS_TAG_NO_PARAMS", default=False)),
        "instrument_internals": asbool(os.getenv("DD_BOTOCORE_INSTRUMENT_INTERNALS", default=False)),
        "empty_poll_enabled": asbool(os.getenv("DD_BOTOCORE_EMPTY_POLL_ENABLED", default=True)),
    },
)


def get_version():
    # type: () -> str
    return __version__


class TraceInjectionDecodingError(Exception):
    pass


def get_queue_name(params):
    # type: (str) -> str
    """
    :params: contains the params for the current botocore action

    Return the name of the queue given the params
    """
    queue_url = params["QueueUrl"]
    url = parse.urlparse(queue_url)
    return url.path.rsplit("/", 1)[-1]


def get_pathway(pin, endpoint_service, dsm_identifier, span=None):
    # type: (Pin, str, str, Span) -> str
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
        ["direction:out", "topic:{}".format(dsm_identifier), path_type], span=span
    )
    return pathway.encode_b64()


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
    botocore.client._datadog_patch = True

    wrapt.wrap_function_wrapper("botocore.client", "BaseClient._make_api_call", patched_api_call)
    Pin(service="aws").onto(botocore.client.BaseClient)
    wrapt.wrap_function_wrapper("botocore.parsers", "ResponseParser.parse", patched_lib_fn)
    Pin(service="aws").onto(botocore.parsers.ResponseParser)
    _PATCHED_SUBMODULES.clear()


def unpatch():
    _PATCHED_SUBMODULES.clear()
    if getattr(botocore.client, "_datadog_patch", False):
        botocore.client._datadog_patch = False
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

    operation = None
    params = None
    if args:
        operation = get_argument_value(args, kwargs, 0, "operation_name")
        params = get_argument_value(args, kwargs, 1, "api_params")

    function_vars = {
        "endpoint_name": endpoint_name,
        "operation": operation,
        "params": params,
        "pin": pin,
        "trace_operation": trace_operation,
    }

    if endpoint_name == "kinesis":
        return patched_kinesis_api_call(
            original_func=original_func,
            instance=instance,
            args=args,
            kwargs=kwargs,
            vars=function_vars,
        )
    elif endpoint_name == "sqs":
        return patched_sqs_api_call(
            original_func=original_func,
            instance=instance,
            args=args,
            kwargs=kwargs,
            vars=function_vars,
        )
    else:
        with pin.tracer.trace(
            trace_operation,
            service=schematize_service_name("{}.{}".format(pin.service, endpoint_name)),
            span_type=SpanTypes.HTTP,
        ) as span:
            set_patched_api_call_span_tags(span, instance, args, params, endpoint_name, operation)

            if args:
                if config.botocore["distributed_tracing"]:
                    try:
                        if endpoint_name == "lambda" and operation == "Invoke":
                            inject_trace_to_client_context(params, span)
                            span.name = schematize_cloud_faas_operation(
                                trace_operation, cloud_provider="aws", cloud_service="lambda"
                            )
                        if endpoint_name == "events" and operation == "PutEvents":
                            inject_trace_to_eventbridge_detail(params, span)
                            span.name = schematize_cloud_messaging_operation(
                                trace_operation,
                                cloud_provider="aws",
                                cloud_service="events",
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
                                trace_operation,
                                cloud_provider="aws",
                                cloud_service="sns",
                                direction=SpanDirection.OUTBOUND,
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
                                trace_operation,
                                cloud_provider="aws",
                                cloud_service="sns",
                                direction=SpanDirection.OUTBOUND,
                            )
                    except Exception:
                        log.warning("Unable to inject trace context", exc_info=True)

            try:
                result = original_func(*args, **kwargs)
                set_response_metadata_tags(span, result)
                return result

            except botocore.exceptions.ClientError as e:
                # `ClientError.response` contains the result, so we can still grab response metadata
                set_response_metadata_tags(span, e.response)

                # If we have a status code, and the status code is not an error,
                #   then ignore the exception being raised
                status_code = span.get_tag(http.STATUS_CODE)
                if status_code and not config.botocore.operations[span.resource].is_error_code(int(status_code)):
                    span._ignore_exception(botocore.exceptions.ClientError)
                raise


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


def get_aws_trace_headers(headers_string):
    trace_id_dict = {}
    trace_id_parts = headers_string.split(";")
    for part in trace_id_parts:
        if "=" in part:
            key, value = part.split("=")
            trace_id_dict[key.strip()] = value.strip()
    return trace_id_dict


def extract_DD_context(messages):
    ctx = None
    if len(messages) == 1:
        message = messages[0]
        context_json = extract_trace_context_json(message)
        if context_json is not None:
            ctx = HTTPPropagator().extract(context_json)
    elif len(messages) > 1:
        prev_ctx = None
        for message in messages:
            prev_ctx = ctx
            context_json = extract_trace_context_json(message)
            if context_json is not None:
                ctx = HTTPPropagator().extract(context_json)

            # only want parenting for batches if all contexts are the same
            if prev_ctx is not None and prev_ctx != ctx:
                ctx = None
                break
    return ctx


def extract_trace_context_json(message):
    context_json = None
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
    elif "Attributes" in message and "AWSTraceHeader" in message["Attributes"]:
        # this message contains AWS tracing propagation
        context_json = get_aws_trace_headers(message["Attributes"]["AWSTraceHeader"])

    if context_json is None:
        # AWS SNS holds attributes within message body
        if "Body" in message:
            try:
                body = json.loads(message["Body"])
                return extract_trace_context_json(body)
            except ValueError:
                log.debug("Unable to parse AWS message body.")
    return context_json
