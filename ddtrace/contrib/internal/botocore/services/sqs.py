import json
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import Optional  # noqa:F401

import botocore.client
import botocore.exceptions

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_cloud_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection

from ..utils import extract_DD_json


log = get_logger(__name__)
MAX_INJECTION_DATA_ATTRIBUTES = 10
SERVICE_TYPES = {
    # Use String since changing this to Binary would be a breaking change as other tracers expect this to be a String.
    "sqs": "String",
    # Use Binary since SNS subscription filter policies fail silently with JSON strings
    # https://github.com/DataDog/datadog-lambda-js/pull/269 AWS will encode our value if it sees "Binary"
    "sns": "Binary",
}


def _encode_data(data):
    # NB This method exists solely to enable us to patch the value in tests, since
    # moto doesn't support auto-encoded SNS -> SQS as binary with RawDelivery enabled
    return json.dumps(data)


def add_dd_attributes_to_message(
    data_to_add: Dict[str, str], entry: Dict[str, Any], endpoint_service: Optional[str] = None
) -> None:
    entry.setdefault("MessageAttributes", {})
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
    data_type = SERVICE_TYPES.get(endpoint_service)
    if data_type is not None:
        entry["MessageAttributes"]["_datadog"] = {"DataType": data_type, f"{data_type}Value": _encode_data(data_to_add)}


def update_messages(ctx, endpoint_service: Optional[str] = None) -> None:
    params = ctx["params"]
    if "Entries" in params or "PublishBatchRequestEntries" in params:
        entries = params.get("Entries", params.get("PublishBatchRequestEntries", []))
        if len(entries) == 0:
            log.warning("Skipping injecting Datadog attributes to records, no records available")
            return
    else:
        entries = [None]
    data_to_add = {}
    for entry in entries:
        dispatch_args = [ctx, None, endpoint_service, data_to_add, params]
        if entry is not None:
            dispatch_args.append(entry)
            inject_args = [data_to_add, entry, endpoint_service]
        else:
            inject_args = [data_to_add, params, endpoint_service]
        core.dispatch("botocore.sqs_sns.update_messages", dispatch_args)
        add_dd_attributes_to_message(*inject_args)


def _ensure_datadog_messageattribute_enabled(params):
    if "MessageAttributeNames" not in params:
        params.update({"MessageAttributeNames": ["_datadog"]})
    elif "_datadog" not in params["MessageAttributeNames"]:
        params.update({"MessageAttributeNames": list(params["MessageAttributeNames"]) + ["_datadog"]})


def patched_sqs_api_call(original_func, instance, args, kwargs, function_vars):
    with core.context_with_data("botocore.patched_sqs_api_call.propagated") as parent_ctx:
        return _patched_sqs_api_call(parent_ctx, original_func, instance, args, kwargs, function_vars)


def _patched_sqs_api_call(parent_ctx, original_func, instance, args, kwargs, function_vars):
    params = function_vars.get("params")
    trace_operation = function_vars.get("trace_operation")
    pin = function_vars.get("pin")
    endpoint_name = function_vars.get("endpoint_name")
    operation = function_vars.get("operation")

    func_has_run = False
    func_run_err = None
    result = None

    if operation == "ReceiveMessage":
        _ensure_datadog_messageattribute_enabled(params)

        try:
            func_has_run = True
            core.dispatch(f"botocore.{endpoint_name}.{operation}.pre", [params])
            # run the function to extract possible parent context before creating ExecutionContext
            result = original_func(*args, **kwargs)
            core.dispatch(
                f"botocore.{endpoint_name}.{operation}.post",
                [parent_ctx, params, result, config.botocore.propagation_enabled, extract_DD_json],
            )
        except Exception as e:
            func_run_err = e

    function_is_not_recvmessage = not func_has_run
    received_message_when_polling = func_has_run and parent_ctx.get_item("message_received")
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
    if endpoint_name == "sqs" and operation in ("SendMessage", "SendMessageBatch", "ReceiveMessage"):
        call_name = schematize_cloud_messaging_operation(
            trace_operation,
            cloud_provider="aws",
            cloud_service="sqs",
            direction=SpanDirection.INBOUND if operation == "ReceiveMessage" else SpanDirection.OUTBOUND,
        )
    else:
        call_name = trace_operation

    child_of = parent_ctx.get_item("distributed_context")

    if should_instrument:
        with core.context_with_data(
            "botocore.patched_sqs_api_call",
            parent=parent_ctx,
            span_name=call_name,
            service=schematize_service_name(
                "{}.{}".format(ext_service(pin, int_config=config.botocore), endpoint_name)
            ),
            span_type=SpanTypes.HTTP,
            child_of=child_of if child_of is not None else pin.tracer.context_provider.active(),
            activate=True,
            instance=instance,
            args=args,
            params=params,
            endpoint_name=endpoint_name,
            operation=operation,
            call_trace=False,
            pin=pin,
        ) as ctx, ctx.span:
            core.dispatch("botocore.patched_sqs_api_call.started", [ctx])

            if should_update_messages:
                update_messages(ctx, endpoint_service=endpoint_name)

            try:
                if not func_has_run:
                    core.dispatch(f"botocore.{endpoint_name}.{operation}.pre", [params])
                    result = original_func(*args, **kwargs)
                    core.dispatch(f"botocore.{endpoint_name}.{operation}.post", [params, result])

                core.dispatch("botocore.patched_sqs_api_call.success", [ctx, result])

                if func_run_err:
                    raise func_run_err
                return result
            except botocore.exceptions.ClientError as e:
                core.dispatch(
                    "botocore.patched_sqs_api_call.exception",
                    [
                        ctx,
                        e.response,
                        botocore.exceptions.ClientError,
                        config.botocore.operations[ctx.span.resource].is_error_code,
                    ],
                )
                raise
    elif func_has_run:
        if func_run_err:
            raise func_run_err
        return result
