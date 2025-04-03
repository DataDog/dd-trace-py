import json

from ddtrace.contrib.internal.botocore.services.stepfunctions import update_stepfunction_input
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.trace import Pin


def test_update_stepfunction_input():
    params = {
        "stateMachineArn": "arn:aws:states:us-east-1:425362996713:stateMachine:agocs_inner_state_machine",
        "input": "{}",
    }
    pin = Pin()
    with core.context_with_data(
        "botocore.patched_stepfunctions_api_call",
        span_name="states.command",
        service="aws.states",
        span_type=SpanTypes.HTTP,
        call_key="patched_stepfunctions_api_call",
        instance=None,
        args=(
            "StartExecution",
            {
                "stateMachineArn": "arn:aws:states:us-east-1:425362996713:stateMachine:agocs_inner_state_machine",
                "input": "{}",
            },
        ),
        params=params,
        endpoint_name="states",
        operation="StartExecution",
        pin=pin,
    ) as ctx:
        update_stepfunction_input(ctx, params)
        assert params["input"]
        input_obj = json.loads(params["input"])
        assert "_datadog" in input_obj
        assert "x-datadog-trace-id" in input_obj["_datadog"]
        assert "x-datadog-parent-id" in input_obj["_datadog"]
        assert "x-datadog-tags" in input_obj["_datadog"]
        assert "_dd.p.tid" in input_obj["_datadog"]["x-datadog-tags"]


def test_update_stepfunction_input_does_not_mutate_non_dict_input():
    params = {
        "stateMachineArn": "arn:aws:states:us-east-1:425362996713:stateMachine:agocs_inner_state_machine",
        "input": "hello",
    }
    pin = Pin()
    with core.context_with_data(
        "botocore.patched_stepfunctions_api_call",
        span_name="states.command",
        service="aws.states",
        span_type=SpanTypes.HTTP,
        call_key="patched_stepfunctions_api_call",
        instance=None,
        args=(
            "StartExecution",
            {
                "stateMachineArn": "arn:aws:states:us-east-1:425362996713:stateMachine:agocs_inner_state_machine",
                "input": "hello",
            },
        ),
        params=params,
        endpoint_name="states",
        operation="StartExecution",
        pin=pin,
    ) as ctx:
        update_stepfunction_input(ctx, params)
        assert params["input"]
        assert params["input"] == "hello"

    params = {
        "stateMachineArn": "arn:aws:states:us-east-1:425362996713:stateMachine:agocs_inner_state_machine",
        "input": "[1, 2, 3]",
    }
    pin = Pin()
    with core.context_with_data(
        "botocore.patched_stepfunctions_api_call",
        span_name="states.command",
        service="aws.states",
        span_type=SpanTypes.HTTP,
        call_key="patched_stepfunctions_api_call",
        instance=None,
        args=(
            "StartExecution",
            {
                "stateMachineArn": "arn:aws:states:us-east-1:425362996713:stateMachine:agocs_inner_state_machine",
                "input": "[1, 2, 3]",
            },
        ),
        params=params,
        endpoint_name="states",
        operation="StartExecution",
        pin=pin,
    ) as ctx:
        update_stepfunction_input(ctx, params)
        assert params["input"]
        assert params["input"] == "[1, 2, 3]"
