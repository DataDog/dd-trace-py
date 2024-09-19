from typing import Any
from typing import Dict
from typing import List

from ddtrace._trace._span_pointer import _SpanPointerDescription
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace._trace._span_pointer import _standard_hashing_function
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


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

        # The ETag is surrounded by double quotes for some reason.
        if etag.startswith('"') and etag.endswith('"'):
            etag = etag[1:-1]

    except KeyError as e:
        log.warning(
            "missing a parameter or response field required to make span pointer for S3.PutObject: %s",
            str(e),
        )
        return []

    try:
        return [
            _aws_s3_object_span_pointer_description(
                pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                bucket=bucket,
                key=key,
                etag=etag,
            )
        ]
    except Exception as e:
        log.warning(
            "failed to generate S3.PutObject span pointer: %s",
            str(e),
        )
        return []


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
    if '"' in etag:
        # Some AWS API endpoints put the ETag in double quotes. We expect the
        # calling code to have correctly fixed this already.
        raise ValueError(f"ETag should not have double quotes: {etag}")

    return _standard_hashing_function(
        bucket.encode("ascii"),
        key.encode("utf-8"),
        etag.encode("ascii"),
    )
