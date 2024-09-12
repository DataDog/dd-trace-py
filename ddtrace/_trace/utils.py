from hashlib import sha256
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional

from ddtrace import Span
from ddtrace import config
from ddtrace._trace._span_link import _SpanPointerDirection
from ddtrace.constants import _ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.ext import SpanKind
from ddtrace.ext import aws
from ddtrace.ext import http
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.propagation.http import HTTPPropagator


def set_botocore_patched_api_call_span_tags(span: Span, instance, args, params, endpoint_name, operation):
    span.set_tag_str(COMPONENT, config.botocore.integration_name)
    # set span.kind to the type of request being performed
    span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span.set_tag(SPAN_MEASURED_KEY)

    if args:
        # DEV: join is the fastest way of concatenating strings that is compatible
        # across Python versions (see
        # https://stackoverflow.com/questions/1316887/what-is-the-most-efficient-string-concatenation-method-in-python)
        span.resource = ".".join((endpoint_name, operation.lower()))
        span.set_tag("aws_service", endpoint_name)

        if params and not config.botocore["tag_no_params"]:
            aws._add_api_param_span_tags(span, endpoint_name, params)

    else:
        span.resource = endpoint_name

    region_name = deep_getattr(instance, "meta.region_name")

    span.set_tag_str("aws.agent", "botocore")
    if operation is not None:
        span.set_tag_str("aws.operation", operation)
    if region_name is not None:
        span.set_tag_str("aws.region", region_name)
        span.set_tag_str("region", region_name)

    # set analytics sample rate
    span.set_tag(_ANALYTICS_SAMPLE_RATE_KEY, config.botocore.get_analytics_sample_rate())


def set_botocore_response_metadata_tags(
    span: Span, result: Dict[str, Any], is_error_code_fn: Optional[Callable] = None
) -> None:
    if not result or not result.get("ResponseMetadata"):
        return
    response_meta = result["ResponseMetadata"]

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


def extract_DD_context_from_messages(messages, extract_from_message: Callable):
    ctx = None
    if len(messages) >= 1:
        message = messages[0]
        context_json = extract_from_message(message)
        if context_json is not None:
            child_of = HTTPPropagator.extract(context_json)
            if child_of.trace_id is not None:
                ctx = child_of
    return ctx


class SpanPointerDescription(NamedTuple):
    # Not to be confused with ddtrace._trace._span_link._SpanPointer. This
    # object describes a span pointer without coupling the botocore code to the
    # actual mechanics of tracing. We can crete events with these span pointer
    # descriptions in botocore. Then our tracing code and pick up these events
    # and create actual span pointers on the spans. Or presumably do other
    # things, too.
    pointer_kind: str
    pointer_direction: _SpanPointerDirection
    pointer_hash: str
    extra_attributes: Dict[str, Any]


# TODO: maybe move this and other botocore stuff to a botocore utils?


def extract_span_pointers_from_successful_botocore_response(
    endpoint_name: str,
    operation_name: str,
    request_parameters: Dict[str, Any],
    response: Dict[str, Any],
) -> List[SpanPointerDescription]:
    if endpoint_name == "s3":
        return _extract_span_pointers_for_s3_response(operation_name, request_parameters, response)

    return []


def _extract_span_pointers_for_s3_response(
    operation_name: str,
    request_parameters: Dict[str, Any],
    response: Dict[str, Any],
) -> List[SpanPointerDescription]:
    if operation_name == "PutObject":
        return _extract_span_pointers_for_s3_put_object_response(request_parameters, response)

    return []


def _extract_span_pointers_for_s3_put_object_response(
    request_parameters: Dict[str, Any],
    response: Dict[str, Any],
) -> List[SpanPointerDescription]:
    # Endpoint Reference:
    # https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html

    try:
        bucket = request_parameters["Bucket"]
        key = request_parameters["Key"]
        etag = response["ETag"]
    except KeyError:
        # Maybe we log this strange situation? Those fields are supposed to be
        # required.
        return []

    return [
        _aws_s3_object_span_pointer_description(
            pointer_direction=_SpanPointerDirection.DOWNSTREAM,
            bucket=bucket,
            key=key,
            etag=etag,
        )
    ]


def _aws_s3_object_span_pointer_description(
    pointer_direction: _SpanPointerDirection,
    bucket: str,
    key: str,
    etag: str,
) -> SpanPointerDescription:
    return SpanPointerDescription(
        pointer_kind="aws.s3.object",
        pointer_direction=pointer_direction,
        pointer_hash=_aws_s3_object_span_pointer_hash(bucket, key, etag),
        extra_attributes={},
    )


def _aws_s3_object_span_pointer_hash(bucket: str, key: str, etag: str) -> str:
    return _standard_hashing_function(
        bucket.encode("ascii"),
        key.encode("utf-8"),
        etag.encode("ascii"),
    )


def _standard_hashing_function(*elements: bytes) -> str:
    # TODO: find another home for this function. It's not actually specific to
    # boto.
    separator = b"|"
    bits_per_hex_digit = 4
    desired_bits = 128
    hex_digits = desired_bits // bits_per_hex_digit

    hex_digest = sha256(separator.join(elements)).hexdigest()
    assert len(hex_digest) >= hex_digits

    return hex_digest[:hex_digits]
