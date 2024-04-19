import json
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import Optional  # noqa:F401

import botocore.client
import botocore.exceptions

from ddtrace import config
from ddtrace.contrib.botocore.utils import extract_DD_context
from ddtrace.contrib.botocore.utils import set_patched_api_call_span_tags
from ddtrace.contrib.botocore.utils import set_response_metadata_tags
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.compat import time_ns
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_cloud_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


log = get_logger(__name__)
MAX_INJECTION_DATA_ATTRIBUTES = 10


def _encode_data(trace_data):
    """
    This method exists solely to enable us to patch the value in tests, since
    moto doesn't support auto-encoded SNS -> SQS as binary with RawDelivery enabled
    """
    return json.dumps(trace_data)


def inject_trace_data_to_message_attributes(
    trace_data: Dict[str, str], entry: Dict[str, Any], endpoint_service: Optional[str] = None
) -> None:
    """
    :trace_data: trace headers and DSM pathway to be stored in the entry's MessageAttributes
    :entry: an SQS or SNS record
    :endpoint_service: endpoint of message, "sqs" or "sns"
    Inject trace headers and DSM info into the SQS or SNS record's MessageAttributes
    """
    entry.setdefault("MessageAttributes", {})
    data_type = None
    if len(entry["MessageAttributes"]) >= MAX_INJECTION_DATA_ATTRIBUTES:
        log.warning(
            "skipping trace injection, max number (%d) of MessageAttributes exceeded", MAX_INJECTION_DATA_ATTRIBUTES
        )
        return
    if endpoint_service not in ("sqs", "sns"):
        log.debug(
            "skipping trace injection, endpoint service is not SNS or SQS.",
            extra=dict(endpoint_service=endpoint_service),
        )
        return
    if endpoint_service == "sqs":
        # Use String since changing this to Binary would be a breaking
        # change as other tracers expect this to be a String.
        data_type = "String"
    elif endpoint_service == "sns":
        # Use Binary since SNS subscription filter policies fail silently
        # with JSON strings https://github.com/DataDog/datadog-lambda-js/pull/269
        # AWS will encode our value if it sees "Binary"
        data_type = "Binary"
    if data_type is not None:
        entry["MessageAttributes"]["_datadog"] = {"DataType": data_type, f"{data_type}Value": _encode_data(trace_data)}


def inject_trace_to_sqs_or_sns_batch_message(ctx, params: Any, span, endpoint_service: Optional[str] = None) -> None:
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated
    :endpoint_service: endpoint of message, "sqs" or "sns"
    Inject trace headers info into MessageAttributes for all SQS or SNS records inside a batch
    """

    trace_data = {}

    # An entry here is an SNS or SQS record, and depending on how it was published,
    # it could either show up under Entries (in case of PutRecords),
    # or PublishBatchRequestEntries (in case of PublishBatch).
    entries = params.get("Entries", params.get("PublishBatchRequestEntries", []))
    if len(entries) != 0:
        for entry in entries:
            core.dispatch("botocore.sqs_sns.update_messages", [ctx, span, endpoint_service, trace_data, params, entry])
            inject_trace_data_to_message_attributes(trace_data, entry, endpoint_service)
    else:
        log.warning("Skipping injecting Datadog attributes to records, no records available")


def inject_trace_to_sqs_or_sns_message(ctx, params: Any, span, endpoint_service: Optional[str] = None) -> None:
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated
    :endpoint_service: endpoint of message, "sqs" or "sns"
    Inject trace headers info into MessageAttributes for the SQS or SNS record
    """
    trace_data = {}
    core.dispatch("botocore.sqs_sns.update_messages", [ctx, span, endpoint_service, trace_data, params])
    inject_trace_data_to_message_attributes(trace_data, params, endpoint_service)


def _ensure_datadog_messageattribute_enabled(params):
    if "MessageAttributeNames" not in params:
        params.update({"MessageAttributeNames": ["_datadog"]})
    elif "_datadog" not in params["MessageAttributeNames"]:
        params.update({"MessageAttributeNames": list(params["MessageAttributeNames"]) + ["_datadog"]})


def patched_sqs_api_call(original_func, instance, args, kwargs, function_vars):
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

    if operation == "ReceiveMessage":
        _ensure_datadog_messageattribute_enabled(params)

        try:
            start_ns = time_ns()
            func_run = True
            core.dispatch(f"botocore.{endpoint_name}.{operation}.pre", [params])
            # run the function to extract possible parent context before starting span
            result = original_func(*args, **kwargs)
            core.dispatch(f"botocore.{endpoint_name}.{operation}.post", [params, result])
        except Exception as e:
            func_run_err = e
        if result is not None and "Messages" in result and len(result["Messages"]) >= 1:
            message_received = True
            if config.botocore.propagation_enabled:
                child_of = extract_DD_context(result["Messages"])

    function_is_not_recvmessage = not func_run
    received_message_when_polling = func_run and message_received
    instrument_empty_poll_calls = config.botocore.empty_poll_enabled
    should_instrument = (
        received_message_when_polling or instrument_empty_poll_calls or function_is_not_recvmessage or func_run_err
    )
    should_update_messages = (
        args
        and config.botocore["distributed_tracing"]
        and endpoint_name == "sqs"
        and operation in ("SendMessage", "SendMessageBatch")
    )
    message_update_function = inject_trace_to_sqs_or_sns_message
    if operation == "SendMessageBatch":
        message_update_function = inject_trace_to_sqs_or_sns_batch_message
    if endpoint_name == "sqs" and operation in ("SendMessage", "SendMessageBatch", "ReceiveMessage"):
        span_name = schematize_cloud_messaging_operation(
            trace_operation,
            cloud_provider="aws",
            cloud_service="sqs",
            direction=SpanDirection.INBOUND if operation == "ReceiveMessage" else SpanDirection.OUTBOUND,
        )
    else:
        span_name = trace_operation
    if should_instrument:
        with core.context_with_data(
            "botocore.patched_sqs_api_call",
            span_name=span_name,
            service=schematize_service_name("{}.{}".format(pin.service, endpoint_name)),
            span_type=SpanTypes.HTTP,
            child_of=child_of if child_of is not None else pin.tracer.context_provider.active(),
            activate=True,
            context_started_callback=set_patched_api_call_span_tags,
            instance=instance,
            args=args,
            params=params,
            endpoint_name=endpoint_name,
            operation=operation,
            call_trace=False,
            call_key="instrumented_sqs_call",
            pin=pin,
        ) as ctx, ctx.get_item(ctx.get_item("call_key")) as span:
            core.dispatch("botocore.patched_sqs_api_call.started", [ctx])

            # we need this since we may have ran the wrapped operation before starting the span
            # we need to ensure the span start time is correct
            if start_ns is not None and func_run:
                span.start_ns = start_ns

            if should_update_messages:
                message_update_function(
                    ctx,
                    params,
                    None,
                    endpoint_service=endpoint_name,
                )

            try:
                if not func_run:
                    core.dispatch(f"botocore.{endpoint_name}.{operation}.pre", [params])
                    result = original_func(*args, **kwargs)
                    core.dispatch(f"botocore.{endpoint_name}.{operation}.post", [params, result])

                core.dispatch("botocore.patched_sqs_api_call.success", [ctx, result, set_response_metadata_tags])

                if func_run_err:
                    raise func_run_err
                return result
            except botocore.exceptions.ClientError as e:
                core.dispatch(
                    "botocore.patched_sqs_api_call.exception",
                    [ctx, e.response, botocore.exceptions.ClientError, set_response_metadata_tags],
                )
                raise
    elif func_run:
        if func_run_err:
            raise func_run_err
        return result
