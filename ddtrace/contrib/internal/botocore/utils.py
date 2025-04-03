"""
Trace queries monitoring to aws api done via botocore client
"""
import base64
import json
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer


log = get_logger(__name__)

TWOFIFTYSIX_KB = 1 << 18
MAX_EVENTBRIDGE_DETAIL_SIZE = TWOFIFTYSIX_KB
LINE_BREAK = "\n"


def get_json_from_str(data_str: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    data_obj = json.loads(data_str)

    if data_str.endswith(LINE_BREAK):
        return LINE_BREAK, data_obj
    return None, data_obj


def get_kinesis_data_object(data: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    :data: the data from a kinesis stream
    The data from a kinesis stream comes as a string (could be json, base64 encoded, etc.)
    We support injecting our trace context in the following three cases:
    - json string
    - byte encoded json string
    - base64 encoded json string
    If it's none of these, then we return None
    """
    # check if data is a json string
    try:
        return get_json_from_str(data)
    except Exception as e:
        telemetry_writer.add_integration_error_log(
            "Kinesis data is not a JSON string. Trying Byte encoded JSON string", e
        )

    # check if data is an encoded json string
    try:
        data_str = data.decode("ascii")
        return get_json_from_str(data_str)
    except Exception as e:
        telemetry_writer.add_integration_error_log(
            "Kinesis data is not a JSON string encoded. Trying Base64 encoded JSON string", e
        )

    # check if data is a base64 encoded json string
    try:
        data_str = base64.b64decode(data).decode("ascii")
        return get_json_from_str(data_str)
    except Exception as e:
        telemetry_writer.add_integration_error_log("Unable to parse payload, unable to inject trace context", e)

    return None, None


def update_eventbridge_detail(ctx: ExecutionContext) -> None:
    params = ctx["params"]
    if "Entries" not in params:
        log.warning("Unable to inject context. The Event Bridge event had no Entries.")
        return

    for entry in params["Entries"]:
        detail = {}
        if "Detail" in entry:
            try:
                detail = json.loads(entry["Detail"])
            except ValueError as e:
                telemetry_writer.add_integration_error_log("Detail is not a valid JSON string", e, warning=True)
                continue

        detail["_datadog"] = {}
        core.dispatch("botocore.eventbridge.update_messages", [ctx, None, None, detail["_datadog"], None])
        detail_json = json.dumps(detail)

        # check if detail size will exceed max size with headers
        detail_size = len(detail_json)
        if detail_size >= MAX_EVENTBRIDGE_DETAIL_SIZE:
            log.warning("Detail with trace injection (%s) exceeds limit (%s)", detail_size, MAX_EVENTBRIDGE_DETAIL_SIZE)
            continue

        entry["Detail"] = detail_json


def update_client_context(ctx: ExecutionContext) -> None:
    trace_headers = {}
    core.dispatch("botocore.client_context.update_messages", [ctx, None, None, trace_headers, None])
    client_context_object = {}
    params = ctx["params"]
    if "ClientContext" in params:
        try:
            client_context_json = base64.b64decode(params["ClientContext"]).decode("utf-8")
            client_context_object = json.loads(client_context_json)
        except Exception as e:
            telemetry_writer.add_integration_error_log(
                "malformed client_context=%s" % params["ClientContext"], e, warning=True
            )
            return
    modify_client_context(client_context_object, trace_headers)
    try:
        json_context = json.dumps(client_context_object).encode("utf-8")
    except Exception as e:
        telemetry_writer.add_integration_error_log(
            "unable to encode modified client context as json: %s" % client_context_object, e, warning=True
        )
        return
    params["ClientContext"] = base64.b64encode(json_context).decode("utf-8")


def modify_client_context(client_context_object, trace_headers):
    if config.botocore["invoke_with_legacy_context"]:
        trace_headers = {"_datadog": trace_headers}

    if "custom" in client_context_object:
        client_context_object["custom"].update(trace_headers)
    else:
        client_context_object["custom"] = trace_headers


def extract_DD_json(message):
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
                    return extract_DD_json(body)
                except ValueError as e:
                    telemetry_writer.add_integration_error_log("Unable to parse AWS message body", e)
    except Exception as e:
        telemetry_writer.add_integration_error_log("Unable to parse AWS message attributes for Datadog Context", e)
    return context_json
