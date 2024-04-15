from datetime import datetime
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional  # noqa:F401

import botocore.client
import botocore.exceptions

from ddtrace import config
from ddtrace._trace.context import Context
from ddtrace.internal import core
from ddtrace.internal.schema.span_attribute_schema import SpanDirection

from ....ext import SpanTypes
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


ONE_MB = 1 << 20
MAX_KINESIS_DATA_SIZE = ONE_MB


class TraceInjectionSizeExceed(Exception):
    pass


def inject_trace_to_kinesis_stream_data(
    record: Dict[str, Any], context_to_propagate: Context, stream: str, inject_trace_context: bool = True
) -> None:
    """
    :record: contains args for the current botocore action, Kinesis record is at index 1
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
                HTTPPropagator.inject(context_to_propagate, data_obj["_datadog"])

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


def inject_trace_to_kinesis_stream(
    params: List[Any], context_to_propagate: Context, inject_trace_context: bool = True
) -> None:
    """
    :params: contains the params for the current botocore action
    :inject_trace_context: whether to inject DataDog trace context
    Max data size per record is 1MB (https://aws.amazon.com/kinesis/data-streams/faqs/)
    """
    stream = params.get("StreamARN", params.get("StreamName", ""))
    if "Records" in params:
        records = params["Records"]

        if records:
            for i in range(0, len(records)):
                inject_trace_to_kinesis_stream_data(
                    records[i], context_to_propagate, stream, inject_trace_context=inject_trace_context and i == 0
                )
    elif "Data" in params:
        inject_trace_to_kinesis_stream_data(
            params, context_to_propagate, stream, inject_trace_context=inject_trace_context
        )


def patched_kinesis_api_call(original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    trace_operation = function_vars.get("trace_operation")
    pin = function_vars.get("pin")
    endpoint_name = function_vars.get("endpoint_name")
    operation = function_vars.get("operation")

    message_received = False
    is_getrecords_call = False
    getrecords_error = None
    child_of = None
    start_ns = None
    result = None

    if operation == "GetRecords":
        try:
            start_ns = time_ns()
            is_getrecords_call = True
            core.dispatch(f"botocore.{endpoint_name}.{operation}.pre", [params])
            result = original_func(*args, **kwargs)

            records = result["Records"]

            for record in records:
                _, data_obj = get_kinesis_data_object(record["Data"])
                time_estimate = record.get("ApproximateArrivalTimestamp", datetime.now()).timestamp()
                core.dispatch(
                    f"botocore.{endpoint_name}.{operation}.post",
                    [params, time_estimate, data_obj.get("_datadog"), record],
                )

        except Exception as e:
            getrecords_error = e
        if result is not None and "Records" in result and len(result["Records"]) >= 1:
            message_received = True
            if config.botocore.propagation_enabled:
                child_of = extract_DD_context(result["Records"])

    function_is_not_getrecords = not is_getrecords_call
    received_message_when_polling = is_getrecords_call and message_received
    instrument_empty_poll_calls = config.botocore.empty_poll_enabled
    should_instrument = (
        received_message_when_polling or instrument_empty_poll_calls or function_is_not_getrecords or getrecords_error
    )
    if should_instrument:
        with core.context_with_data(
            "botocore.patched_kinesis_api_call",
            instance=instance,
            args=args,
            params=params,
            endpoint_name=endpoint_name,
            operation=operation,
            service=schematize_service_name("{}.{}".format(pin.service, endpoint_name)),
            call_trace=False,
            context_started_callback=set_patched_api_call_span_tags,
            pin=pin,
            span_name=trace_operation,
            span_type=SpanTypes.HTTP,
            child_of=child_of if child_of is not None else pin.tracer.context_provider.active(),
            activate=True,
            func_run=is_getrecords_call,
            start_ns=start_ns,
            call_key="patched_kinesis_api_call",
        ) as ctx, ctx.get_item(ctx.get_item("call_key")) as span:
            core.dispatch("botocore.patched_kinesis_api_call.started", [ctx])

            if config.botocore["distributed_tracing"] or config._data_streams_enabled:
                try_inject_DD_context(
                    endpoint_name,
                    operation,
                    params,
                    span,
                    trace_operation,
                    inject_trace_context=bool(config.botocore["distributed_tracing"]),
                )

            try:
                if not is_getrecords_call:
                    core.dispatch(f"botocore.{endpoint_name}.{operation}.pre", [params])
                    result = original_func(*args, **kwargs)
                    core.dispatch(f"botocore.{endpoint_name}.{operation}.post", [params, result])

                if getrecords_error:
                    raise getrecords_error

                core.dispatch("botocore.patched_kinesis_api_call.success", [ctx, result, set_response_metadata_tags])
                return result

            except botocore.exceptions.ClientError as e:
                core.dispatch(
                    "botocore.patched_kinesis_api_call.exception",
                    [ctx, e.response, botocore.exceptions.ClientError, set_response_metadata_tags],
                )
                raise
    elif is_getrecords_call:
        if getrecords_error:
            raise getrecords_error
        return result


def try_inject_DD_context(endpoint_name, operation, params, span, trace_operation, inject_trace_context):
    try:
        if endpoint_name == "kinesis" and operation in {"PutRecord", "PutRecords"}:
            inject_trace_to_kinesis_stream(params, span.context, inject_trace_context=inject_trace_context)
            span.name = schematize_cloud_messaging_operation(
                trace_operation,
                cloud_provider="aws",
                cloud_service="kinesis",
                direction=SpanDirection.OUTBOUND,
            )
    except Exception:
        log.warning("Unable to inject trace context", exc_info=True)
