from typing import Any
from typing import Callable
from typing import Dict
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
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.internal.serverless import in_aws_lambda


_PAYLOAD_TAGGER = AWSPayloadTagging()


# Helper to build AWS hostname from service, region and parameters
def _derive_peer_hostname(service: str, region: str, params: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Return hostname for given AWS service according to Datadog peer hostname rules.

    Logic mirrors the JS mapping provided by the user:

        events   -> events.<region>.amazonaws.com
        sqs      -> sqs.<region>.amazonaws.com
        sns      -> sns.<region>.amazonaws.com
        kinesis  -> kinesis.<region>.amazonaws.com
        dynamodb -> dynamodb.<region>.amazonaws.com
        s3       -> <bucket>.s3.<region>.amazonaws.com (if Bucket param present)
                   s3.<region>.amazonaws.com          (otherwise)

    Unknown services or missing region return ``None``.
    """

    if not region:
        return None

    aws_service = service.lower()

    if aws_service in {"eventbridge", "events"}:
        return f"events.{region}.amazonaws.com"
    if aws_service == "sqs":
        return f"sqs.{region}.amazonaws.com"
    if aws_service == "sns":
        return f"sns.{region}.amazonaws.com"
    if aws_service == "kinesis":
        return f"kinesis.{region}.amazonaws.com"
    if aws_service in {"dynamodb", "dynamodbdocument"}:
        return f"dynamodb.{region}.amazonaws.com"
    if aws_service == "s3":
        bucket = params.get("Bucket") if params else None
        if bucket:
            return f"{bucket}.s3.{region}.amazonaws.com"
        return f"s3.{region}.amazonaws.com"

    return None


def set_botocore_patched_api_call_span_tags(span: Span, instance, args, params, endpoint_name, operation):
    span.set_tag_str(COMPONENT, config.botocore.integration_name)
    # set span.kind to the type of request being performed
    span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span.set_tag(_SPAN_MEASURED_KEY)

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

    span.set_tag_str("aws.agent", "botocore")
    if operation is not None:
        span.set_tag_str("aws.operation", operation)
    if region_name is not None:
        span.set_tag_str("aws.region", region_name)
        span.set_tag_str("region", region_name)

        # Derive peer hostname only in serverless environments to avoid
        # unnecessary tag noise in traditional hosts/containers.
        if in_aws_lambda():
            hostname = _derive_peer_hostname(endpoint_name, region_name, params)
            if hostname:
                span.set_tag_str("peer.service", hostname)


def set_botocore_response_metadata_tags(
    span: Span, result: Dict[str, Any], is_error_code_fn: Optional[Callable] = None
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
        span.set_tag(http.STATUS_CODE, status_code)

        # Mark this span as an error if requested
        if is_error_code_fn is not None and is_error_code_fn(int(status_code)):
            span.error = 1

    if "RetryAttempts" in response_meta:
        span.set_tag("retry_attempts", response_meta["RetryAttempts"])

    if "RequestId" in response_meta:
        span.set_tag_str("aws.requestid", response_meta["RequestId"])
