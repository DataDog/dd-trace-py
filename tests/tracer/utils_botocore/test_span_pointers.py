import logging
import re
from typing import List
from typing import NamedTuple
from typing import Optional

import mock
import pytest

from ddtrace._trace._span_pointer import _SpanPointerDescription
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace._trace.utils_botocore.span_pointers import _aws_s3_object_span_pointer_hash
from ddtrace._trace.utils_botocore.span_pointers import extract_span_pointers_from_successful_botocore_response


class TestS3ObjectPointer:
    class HashingCase(NamedTuple):
        name: str
        bucket: str
        key: str
        etag: str
        pointer_hash: str

    @pytest.mark.parametrize(
        "hashing_case",
        [
            HashingCase(
                name="a basic S3 object",
                bucket="some-bucket",
                key="some-key.data",
                etag="ab12ef34",
                pointer_hash="e721375466d4116ab551213fdea08413",
            ),
            HashingCase(
                name="an S3 object with a non-ascii key",
                bucket="some-bucket",
                key="some-key.你好",
                etag="ab12ef34",
                pointer_hash="d1333a04b9928ab462b5c6cadfa401f4",
            ),
            HashingCase(
                name="a multipart-uploaded S3 object",
                bucket="some-bucket",
                key="some-key.data",
                etag="ab12ef34-5",
                pointer_hash="2b90dffc37ebc7bc610152c3dc72af9f",
            ),
        ],
        ids=lambda case: case.name,
    )
    def test_hashing(self, hashing_case: HashingCase) -> None:
        assert (
            _aws_s3_object_span_pointer_hash(
                bucket=hashing_case.bucket,
                key=hashing_case.key,
                etag=hashing_case.etag,
            )
            == hashing_case.pointer_hash
        )


class TestBotocoreSpanPointers:
    class PointersCase(NamedTuple):
        name: str
        endpoint_name: str
        operation_name: str
        request_parameters: dict
        response: dict
        expected_pointers: List[_SpanPointerDescription]
        expected_warning_regex: Optional[str]

    @pytest.mark.parametrize(
        "pointers_case",
        [
            PointersCase(
                name="unknown endpoint",
                endpoint_name="unknown",
                operation_name="does not matter",
                request_parameters={},
                response={},
                expected_pointers=[],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="unknown s3 operation",
                endpoint_name="s3",
                operation_name="unknown",
                request_parameters={},
                response={},
                expected_pointers=[],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="malformed s3.PutObject, missing bucket",
                endpoint_name="s3",
                operation_name="PutObject",
                request_parameters={
                    "Key": "some-key.data",
                },
                response={
                    "ETag": "ab12ef34",
                },
                expected_pointers=[],
                expected_warning_regex=r"missing a parameter or response field .*: 'Bucket'",
            ),
            PointersCase(
                name="malformed s3.PutObject, missing key",
                endpoint_name="s3",
                operation_name="PutObject",
                request_parameters={
                    "Bucket": "some-bucket",
                },
                response={
                    "ETag": "ab12ef34",
                },
                expected_pointers=[],
                expected_warning_regex=r"missing a parameter or response field .*: 'Key'",
            ),
            PointersCase(
                name="malformed s3.PutObject, missing etag",
                endpoint_name="s3",
                operation_name="PutObject",
                request_parameters={
                    "Bucket": "some-bucket",
                    "Key": "some-key.data",
                },
                response={},
                expected_pointers=[],
                expected_warning_regex=r"missing a parameter or response field .*: 'ETag'",
            ),
            PointersCase(
                name="malformed s3.PutObject, impossible non-ascii bucket",
                endpoint_name="s3",
                operation_name="PutObject",
                request_parameters={
                    "Bucket": "some-bucket-你好",
                    "Key": "some-key.data",
                },
                response={
                    "ETag": "ab12ef34",
                },
                expected_pointers=[],
                expected_warning_regex=r".*'ascii' codec can't encode characters.*",
            ),
            PointersCase(
                name="s3.PutObject",
                endpoint_name="s3",
                operation_name="PutObject",
                request_parameters={
                    "Bucket": "some-bucket",
                    "Key": "some-key.data",
                },
                response={
                    "ETag": "ab12ef34",
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.s3.object",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="e721375466d4116ab551213fdea08413",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="s3.PutObject with double quoted ETag",
                endpoint_name="s3",
                operation_name="PutObject",
                request_parameters={
                    "Bucket": "some-bucket",
                    "Key": "some-key.data",
                },
                response={
                    # the ETag can be surrounded by double quotes
                    "ETag": '"ab12ef34"',
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.s3.object",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="e721375466d4116ab551213fdea08413",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
        ],
        ids=lambda case: case.name,
    )
    def test_pointers(self, pointers_case: PointersCase) -> None:
        # We might like to use caplog here but it resulted in inconsistent test
        # behavior, so we have to go a bit deeper.

        with mock.patch.object(logging.Logger, "warning") as mock_logger:
            assert (
                extract_span_pointers_from_successful_botocore_response(
                    endpoint_name=pointers_case.endpoint_name,
                    operation_name=pointers_case.operation_name,
                    request_parameters=pointers_case.request_parameters,
                    response=pointers_case.response,
                )
                == pointers_case.expected_pointers
            )

            if pointers_case.expected_warning_regex is None:
                mock_logger.assert_not_called()

            else:
                mock_logger.asser_called_once()

                (args, kwargs) = mock_logger.call_args
                assert not kwargs
                fmt, other_args = args
                assert re.match(
                    pointers_case.expected_warning_regex,
                    fmt % other_args,
                )
