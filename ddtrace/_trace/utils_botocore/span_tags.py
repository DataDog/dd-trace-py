import os
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace._trace.utils_botocore.aws_payload_tagging import AWSPayloadTagging
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import aws
from ddtrace.ext import http
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.serverless import in_aws_lambda
from ddtrace.internal.utils.formats import deep_getattr


_PAYLOAD_TAGGER = AWSPayloadTagging()

SERVICE_MAP = {
    "eventbridge": "events",
    "events": "events",
    "sqs": "sqs",
    "sns": "sns",
    "kinesis": "kinesis",
    "dynamodb": "dynamodb",
    "dynamodbdocument": "dynamodb",
}


def _derive_peer_service_name(endpoint_name: str, params: Optional[dict[str, Any]]) -> Optional[str]:
    """Return the logical resource name to use as peer.service for known AWS services.

    - sqs               -> queue name (last segment of QueueUrl, or QueueName)
    - sns               -> topic name (last segment of TopicArn)
    - kinesis           -> stream name (StreamName)
    - events/eventbridge -> event bus name (EventBusName from params or first entry)
    - states            -> state machine name (last segment of stateMachineArn)
    - s3                -> bucket name (Bucket)
    - dynamodb          -> table name (TableName)
    - lambda            -> function name (FunctionName)
    - cloudwatch        -> log group name (logGroupName)
    - redshift          -> cluster identifier (ClusterIdentifier)
    """
    print("OLIVIER in _derive_peer_service_name")
    if not params:
        print("NO PARAMS")
        return None

    service = endpoint_name.lower()

    if service == "sqs":
        queue_url = params.get("QueueUrl", "")
        if queue_url and (queue_url.startswith("sqs:") or queue_url.startswith("http")):
            print("QUEUE URL")
            print(queue_url.split("/")[-1])
            return queue_url.split("/")[-1]
        print("QUEUE NAME")
        print(params.get("QueueName"))
        return params.get("QueueName") or None

    if service == "sns":
        topic_arn = params.get("TopicArn", "")
        if topic_arn:
            return topic_arn.split(":")[-1]
        return None

    if service == "kinesis":
        return params.get("StreamName") or None

    if service in ("events", "eventbridge"):
        # PutEvents stores the bus name per entry; fall back to top-level EventBusName
        entries = params.get("Entries")
        if entries and isinstance(entries, list) and entries[0].get("EventBusName"):
            return entries[0]["EventBusName"]
        return params.get("EventBusName") or params.get("Name") or None

    if service == "states":
        arn = params.get("stateMachineArn", "")
        if arn:
            return arn.split(":")[-1]
        return None

    if service == "s3":
        return params.get("Bucket") or None

    if service in ("dynamodb", "dynamodbdocument"):
        return params.get("TableName") or None

    if service == "lambda":
        return params.get("FunctionName") or None

    if service == "cloudwatch":
        return params.get("logGroupName") or None

    if service == "redshift":
        return params.get("ClusterIdentifier") or None

    return None


def _derive_peer_hostname(service: str, region: Optional[str]) -> Optional[str]:
    """Return hostname-based peer.service fallback for services with no resource identifier in params."""
    if not region:
        return None

    aws_service = service.lower()

    if aws_service == "s3":
        return f"s3.{region}.amazonaws.com"

    mapped = SERVICE_MAP.get(aws_service)
    return f"{mapped}.{region}.amazonaws.com" if mapped else None


def set_botocore_patched_api_call_span_tags(span: Span, instance, args, params, endpoint_name, operation):
    print ("OLIVIER in set_botocore_patched_api_call_span_tags")
    print("AWS_LAMBDA_FUNCTION_NAME", repr(os.environ.get("AWS_LAMBDA_FUNCTION_NAME")))
    print("AWS_EXECUTION_ENV", repr(os.environ.get("AWS_EXECUTION_ENV")))
    print("LAMBDA_TASK_ROOT", repr(os.environ.get("LAMBDA_TASK_ROOT")))
    span._set_attribute(COMPONENT, config.botocore.integration_name)
    # set span.kind to the type of request being performed
    span._set_attribute(SPAN_KIND, SpanKind.CLIENT)
    span._set_attribute(_SPAN_MEASURED_KEY, 1)

    if args:
        # DEV: join is the fastest way of concatenating strings that is compatible
        # across Python versions (see
        # https://stackoverflow.com/questions/1316887/what-is-the-most-efficient-string-concatenation-method-in-python)
        span.resource = ".".join((endpoint_name, operation.lower()))
        span.set_tag("aws_service", endpoint_name)

        if params and not config.botocore["tag_no_params"]:
            aws._add_api_param_span_tags(span, endpoint_name, params)

        if config.botocore["payload_tagging_request"] and endpoint_name in config.botocore.get(
            "payload_tagging_services"
        ):
            _PAYLOAD_TAGGER.expand_payload_as_tags(span, params, "aws.request.body")

    else:
        span.resource = endpoint_name

    region_name = deep_getattr(instance, "meta.region_name")

    span._set_attribute("aws.agent", "botocore")
    if operation is not None:
        span._set_attribute("aws.operation", operation)
    if region_name is not None:
        span._set_attribute("aws.region", region_name)
        span._set_attribute("region", region_name)
        span._set_attribute("aws.partition", aws.get_aws_partition(region_name))

    if in_aws_lambda():
        print("OLIVIER in aws lambda")
        peer_service = _derive_peer_service_name(endpoint_name, params) or _derive_peer_hostname(
            endpoint_name, region_name
        )
        if peer_service:
            span._set_attribute("peer.service", peer_service)


def set_botocore_response_metadata_tags(
    span: Span, result: dict[str, Any], is_error_code_fn: Optional[Callable] = None
) -> None:
    if not result or not result.get("ResponseMetadata"):
        return
    response_meta = result["ResponseMetadata"]

    if config.botocore["payload_tagging_response"] and span.get_tag("aws_service") in config.botocore.get(
        "payload_tagging_services"
    ):
        _PAYLOAD_TAGGER.expand_payload_as_tags(span, response_meta, "aws.response.body")

    if "HTTPStatusCode" in response_meta:
        status_code = response_meta["HTTPStatusCode"]
        span._set_attribute(http.STATUS_CODE, status_code)

        # Mark this span as an error if requested
        if is_error_code_fn is not None and is_error_code_fn(int(status_code)):
            span.error = 1

    if "RetryAttempts" in response_meta:
        span.set_tag("retry_attempts", response_meta["RetryAttempts"])

    if "RequestId" in response_meta:
        span._set_attribute("aws.requestid", response_meta["RequestId"])
