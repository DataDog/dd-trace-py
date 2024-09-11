from hashlib import sha256
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple

from ddtrace._trace._span_link import _SpanPointerDirection


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


def extract_span_pointers_from_successful_response(
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
