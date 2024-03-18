from datetime import datetime
import json
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401

import botocore.client
import botocore.exceptions

from ddtrace import Span  # noqa:F401
from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.schema.span_attribute_schema import SpanDirection

from ....ext import SpanTypes
from ....ext import http
from ....internal.compat import time_ns
from ....internal.logger import get_logger
from ....internal.schema import schematize_cloud_messaging_operation
from ....internal.schema import schematize_service_name
from ....pin import Pin  # noqa:F401
from ....propagation.http import HTTPPropagator
from ..utils import extract_DD_context
from ..utils import get_kinesis_data_object
from ..utils import set_patched_api_call_span_tags
from ..utils import set_response_metadata_tags


log = get_logger(__name__)


MAX_KINESIS_DATA_SIZE = 1 << 20  # 1MB


class TraceInjectionSizeExceed(Exception):
    pass


def inject_trace_to_kinesis_stream_data(record, span, stream, inject_trace_context=True):
    # type: (Dict[str, Any], Span, str, bool) -> None
    """
    :record: contains args for the current botocore action, Kinesis record is at index 1
    :span: the span which provides the trace context to be propagated
    :inject_trace_context: whether to inject DataDog trace context
    Inject trace headers and DSM headers into the Kinesis record's Data field in addition to the existing
    data. Only possible if the existing data is JSON string or base64 encoded JSON string
    Max data size per record is 1MB (https://aws.amazon.com/kinesis/data-streams/faqs/)
    """
    if "Data" not in record:
        log.warning("Unable to inject context. The kinesis stream has no data")
        return

    # always inject if Data Stream is enabled, otherwise only inject if distributed tracing is enabled and this is the
    # first record in the payload
    if (config.botocore["distributed_tracing"] and inject_trace_context) or config._data_streams_enabled:
        data = record["Data"]
        line_break, data_obj = get_kinesis_data_object(data)
        if data_obj is not None:
            data_obj["_datadog"] = {}

            if config.botocore["distributed_tracing"] and inject_trace_context:
                HTTPPropagator.inject(span.context, data_obj["_datadog"])

            core.dispatch("botocore.kinesis.start", [stream, data_obj["_datadog"], record])

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


def inject_trace_to_kinesis_stream(params, span, inject_trace_context=True):
    # type: (List[Any], Span, bool) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated
    :inject_trace_context: whether to inject DataDog trace context
    Max data size per record is 1MB (https://aws.amazon.com/kinesis/data-streams/faqs/)
    """
    stream = params.get("StreamARN", params.get("StreamName", ""))
    if "Records" in params:
        records = params["Records"]

        if records:
            for i in range(0, len(records)):
                inject_to_trace = inject_trace_context and i == 0
                inject_trace_to_kinesis_stream_data(
                    records[i], span, stream, inject_trace_context=inject_to_trace
                )  # only inject trace data to first record
    elif "Data" in params:
        inject_trace_to_kinesis_stream_data(params, span, stream, inject_trace_context=inject_trace_context)


def patched_kinesis_api_call(original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    trace_operation = function_vars.get("trace_operation")
    pin = function_vars.get("pin")
    endpoint_name = function_vars.get("endpoint_name")
    operation = function_vars.get("operation")

    message_received = False
    func_run = False
    func_run_err = None
    child_of = None
    start_ns = None
    result = None

    if operation == "GetRecords":
        try:
            start_ns = time_ns()
            func_run = True
            core.dispatch(f"botocore.{endpoint_name}.{operation}.pre", [params])
            result = original_func(*args, **kwargs)

            records = result["Records"]

            # dispatch to DSM to set checkpoints
            for record in records:
                _, data_obj = get_kinesis_data_object(record["Data"])
                time_estimate = record.get("ApproximateArrivalTimestamp", datetime.now()).timestamp()
                core.dispatch(
                    f"botocore.{endpoint_name}.{operation}.post",
                    [params, time_estimate, data_obj.get("_datadog"), record],
                )

        except Exception as e:
            func_run_err = e
        if result is not None and "Records" in result and len(result["Records"]) >= 1:
            message_received = True
            if config.botocore.propagation_enabled:
                child_of = extract_DD_context(result["Records"])

    """
    We only want to create a span for the following cases:
        - not func_run: The function is not `getRecords` and we need to run it
        - func_run and message_received: Received a message when polling
        - config.empty_poll_enabled: We want to trace empty poll operations
        - func_run_err: There was an error when calling the `getRecords` function
    """
    if (func_run and message_received) or config.botocore.empty_poll_enabled or not func_run or func_run_err:
        with pin.tracer.start_span(
            trace_operation,
            service=schematize_service_name("{}.{}".format(pin.service, endpoint_name)),
            span_type=SpanTypes.HTTP,
            child_of=child_of if child_of is not None else pin.tracer.context_provider.active(),
            activate=True,
        ) as span:
            set_patched_api_call_span_tags(span, instance, args, params, endpoint_name, operation)

            # we need this since we may have ran the wrapped operation before starting the span
            # we need to ensure the span start time is correct
            if start_ns is not None and func_run:
                span.start_ns = start_ns

            if config.botocore["distributed_tracing"]:
                try_inject_DD_context(
                    endpoint_name, operation, params, span, trace_operation, inject_trace_context=True
                )

            # we still want to inject DSM context (if DSM is enabled) even if distributed tracing is disabled
            elif not config.botocore["distributed_tracing"] and config._data_streams_enabled:
                try_inject_DD_context(
                    endpoint_name, operation, params, span, trace_operation, inject_trace_context=False
                )

            try:
                if not func_run:
                    core.dispatch(f"botocore.{endpoint_name}.{operation}.pre", [params])
                    result = original_func(*args, **kwargs)
                    core.dispatch(f"botocore.{endpoint_name}.{operation}.post", [params, result])

                # raise error if it was encountered before the span was started
                if func_run_err:
                    raise func_run_err

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
    # return results in the case that we ran the function, but no records were returned and empty
    # poll spans are disabled
    elif func_run:
        if func_run_err:
            raise func_run_err
        return result


def try_inject_DD_context(endpoint_name, operation, params, span, trace_operation, inject_trace_context):
    try:
        if endpoint_name == "kinesis" and operation in {"PutRecord", "PutRecords"}:
            inject_trace_to_kinesis_stream(params, span, inject_trace_context=inject_trace_context)
            span.name = schematize_cloud_messaging_operation(
                trace_operation,
                cloud_provider="aws",
                cloud_service="kinesis",
                direction=SpanDirection.OUTBOUND,
            )
    except Exception:
        log.warning("Unable to inject trace context", exc_info=True)
