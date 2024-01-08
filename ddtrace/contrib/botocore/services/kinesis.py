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
from ddtrace.internal.schema.span_attribute_schema import SpanDirection

from ....ext import SpanTypes
from ....ext import http
from ....internal.logger import get_logger
from ....internal.schema import schematize_cloud_messaging_operation
from ....internal.schema import schematize_service_name
from ....pin import Pin  # noqa:F401
from ....propagation.http import HTTPPropagator
from ..utils import get_kinesis_data_object
from ..utils import get_pathway
from ..utils import set_patched_api_call_span_tags
from ..utils import set_response_metadata_tags


log = get_logger(__name__)


MAX_KINESIS_DATA_SIZE = 1 << 20  # 1MB


class TraceInjectionSizeExceed(Exception):
    pass


def get_stream_arn(params):
    # type: (str) -> str
    """
    :params: contains the params for the current botocore action
    Return the name of the stream given the params
    """
    stream_arn = params.get("StreamARN", "")
    return stream_arn


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


def record_data_streams_path_for_kinesis_stream(pin, params, results, span):
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
            span=span,
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
                get_pathway(pin, "kinesis", stream_arn, span)

    if "Records" in params:
        records = params["Records"]

        if records:
            record = records[0]
            inject_trace_to_kinesis_stream_data(record, span)
    elif "Data" in params:
        inject_trace_to_kinesis_stream_data(params, span)


def patched_kinesis_api_call(original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    trace_operation = function_vars.get("trace_operation")
    pin = function_vars.get("pin")
    endpoint_name = function_vars.get("endpoint_name")
    operation = function_vars.get("operation")

    with pin.tracer.trace(
        trace_operation,
        service=schematize_service_name("{}.{}".format(pin.service, endpoint_name)),
        span_type=SpanTypes.HTTP,
    ) as span:
        set_patched_api_call_span_tags(span, instance, args, params, endpoint_name, operation)

        if args:
            if config.botocore["distributed_tracing"]:
                try:
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
                except Exception:
                    log.warning("Unable to inject trace context", exc_info=True)

        try:
            result = original_func(*args, **kwargs)

            if endpoint_name == "kinesis" and operation == "GetRecords" and config._data_streams_enabled:
                try:
                    record_data_streams_path_for_kinesis_stream(pin, params, result, span)
                except Exception:
                    log.debug("Failed to report data streams monitoring info for kinesis", exc_info=True)

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
