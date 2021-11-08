"""
Trace queries to aws api done via botocore client
"""
import base64
import json

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
from ...internal.utils.formats import get_env
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from ..trace_utils import unwrap


# Original botocore client class
_Botocore_client = botocore.client.BaseClient

ARGS_NAME = ("action", "params", "path", "verb")
TRACED_ARGS = {"params", "path", "verb"}

log = get_logger(__name__)

# Botocore default settings
config._add(
    "botocore",
    {
        "distributed_tracing": asbool(get_env("botocore", "distributed_tracing", default=True)),
        "invoke_with_legacy_context": asbool(get_env("botocore", "invoke_with_legacy_context", default=False)),
    },
)


def inject_trace_data_to_message_attributes(trace_data, entry):
    if "MessageAttributes" not in entry:
        entry["MessageAttributes"] = {}
    # An Amazon SQS message can contain up to 10 metadata attributes.
    if len(entry["MessageAttributes"]) < 10:
        entry["MessageAttributes"]["_datadog"] = {"DataType": "String", "StringValue": json.dumps(trace_data)}
    else:
        log.debug("skipping trace injection, max number (10) of MessageAttributes exceeded")


def inject_trace_to_sqs_batch_message(args, span):
    trace_data = {}
    HTTPPropagator.inject(span.context, trace_data)
    params = args[1]

    for entry in params["Entries"]:
        inject_trace_data_to_message_attributes(trace_data, entry)


def inject_trace_to_sqs_message(args, span):
    trace_data = {}
    HTTPPropagator.inject(span.context, trace_data)
    params = args[1]

    inject_trace_data_to_message_attributes(trace_data, params)


def modify_client_context(client_context_object, trace_headers):
    if config.botocore["invoke_with_legacy_context"]:
        trace_headers = {"_datadog": trace_headers}

    if "custom" in client_context_object:
        client_context_object["custom"].update(trace_headers)
    else:
        client_context_object["custom"] = trace_headers


def inject_trace_to_client_context(args, span):
    trace_headers = {}
    HTTPPropagator.inject(span.context, trace_headers)
    client_context_object = {}
    params = args[1]
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
    Pin(service="aws", app="aws").onto(botocore.client.BaseClient)


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
            # DEV: join is the fastest way of concatenating strings that is compatible
            # across Python versions (see
            # https://stackoverflow.com/questions/1316887/what-is-the-most-efficient-string-concatenation-method-in-python)
            span.resource = ".".join((endpoint_name, operation.lower()))

            if config.botocore["distributed_tracing"]:
                if endpoint_name == "lambda" and operation == "Invoke":
                    inject_trace_to_client_context(args, span)
                if endpoint_name == "sqs" and operation == "SendMessage":
                    inject_trace_to_sqs_message(args, span)
                if endpoint_name == "sqs" and operation == "SendMessageBatch":
                    inject_trace_to_sqs_batch_message(args, span)

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
