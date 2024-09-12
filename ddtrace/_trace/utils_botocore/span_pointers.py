from typing import Any
from typing import Dict
from typing import List

from ddtrace._trace._span_pointers import _SpanPointerDescription
from ddtrace._trace._span_pointers import _SpanPointerDirection
from ddtrace._trace._span_pointers import _standard_hashing_function


def extract_span_pointers_from_successful_botocore_response(
    endpoint_name: str,
    operation_name: str,
    request_parameters: Dict[str, Any],
    response: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    if endpoint_name == "s3":
        return _extract_span_pointers_for_s3_response(operation_name, request_parameters, response)

    return []


def _extract_span_pointers_for_s3_response(
    operation_name: str,
    request_parameters: Dict[str, Any],
    response: Dict[str, Any],
) -> List[_SpanPointerDescription]:
    if operation_name == "PutObject":
        return _extract_span_pointers_for_s3_put_object_response(request_parameters, response)

    return []


def _extract_span_pointers_for_s3_put_object_response(
    request_parameters: Dict[str, Any],
    response: Dict[str, Any],
) -> List[_SpanPointerDescription]:
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
) -> _SpanPointerDescription:
    return _SpanPointerDescription(
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
