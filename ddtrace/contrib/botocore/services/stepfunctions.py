import json
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401

import botocore.exceptions

from ddtrace import Span  # noqa:F401
from ddtrace import config
from ddtrace.ext import http
from ddtrace.propagation.http import HTTPPropagator

from ....ext import SpanTypes
from ....internal.logger import get_logger
from ....internal.schema import SpanDirection
from ....internal.schema import schematize_cloud_messaging_operation
from ....internal.schema import schematize_service_name
from ..utils import set_patched_api_call_span_tags
from ..utils import set_response_metadata_tags


log = get_logger(__name__)


def inject_trace_to_stepfunction_input(params, span):
    # type: (Any, Span) -> None
    """
    :params: contains the params for the current botocore action
    :span: the span which provides the trace context to be propagated

    Inject the trace headers into the StepFunction input if the input is a JSON string
    """
    if "input" not in params:
        log.warning("Unable to inject context. The StepFunction input had no input.")
        return

    if params["input"] is None:
        log.warning("Unable to inject context. The StepFunction input was None.")
        return

    elif isinstance(params["input"], dict):
        if "_datadog" in params["input"]:
            log.warning("Input already has trace context.")
            return
        params["input"]["_datadog"] = {}
        HTTPPropagator.inject(span.context, params["input"]["_datadog"])
        return

    elif isinstance(params["input"], str):
        try:
            input_obj = json.loads(params["input"])
        except ValueError:
            log.warning("Input is not a valid JSON string")
            return

        if isinstance(input_obj, dict):
            input_obj["_datadog"] = {}
            HTTPPropagator.inject(span.context, input_obj["_datadog"])
            input_json = json.dumps(input_obj)

            params["input"] = input_json
            return
        else:
            log.warning("Unable to inject context. The StepFunction input was not a dict.")
            return

    else:
        log.warning("Unable to inject context. The StepFunction input was not a dict or a JSON string.")


def patched_stepfunction_api_call(original_func, instance, args, kwargs: Dict, function_vars: Dict):
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
                    if endpoint_name == "states" and operation in {"StartExecution", "StartSyncExecution"}:
                        inject_trace_to_stepfunction_input(params, span)
                        span.name = schematize_cloud_messaging_operation(
                            trace_operation,
                            cloud_provider="aws",
                            cloud_service="stepfunctions",
                            direction=SpanDirection.OUTBOUND,
                        )
                except Exception:
                    log.warning("Unable to inject trace context", exc_info=True)

        try:
            return original_func(*args, **kwargs)
        except botocore.exceptions.ClientError as e:
            set_response_metadata_tags(span, e.response)

            # If we have a status code, and the status code is not an error,
            #   then ignore the exception being raised
            status_code = span.get_tag(http.STATUS_CODE)
            if status_code and not config.botocore.operations[span.resource].is_error_code(int(status_code)):
                span._ignore_exception(botocore.exceptions.ClientError)
            raise
