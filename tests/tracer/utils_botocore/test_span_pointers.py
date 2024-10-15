import logging
import re
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional

import mock
import pytest

from ddtrace._trace._span_pointer import _SpanPointerDescription
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace._trace.utils_botocore.span_pointers import extract_span_pointers_from_successful_botocore_response
from ddtrace._trace.utils_botocore.span_pointers.dynamodb import _aws_dynamodb_item_span_pointer_hash
from ddtrace._trace.utils_botocore.span_pointers.s3 import _aws_s3_object_span_pointer_hash


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


class TestDynamodbItemPointer:
    class HashingCase(NamedTuple):
        name: str
        table_name: str
        primary_key: Dict[str, Dict[str, str]]
        pointer_hash: str

    @pytest.mark.parametrize(
        "hashing_case",
        [
            HashingCase(
                name="one string primary key",
                table_name="some-table",
                primary_key={"some-key": {"S": "some-value"}},
                pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
            ),
            HashingCase(
                name="one binary primary key",
                table_name="some-table",
                primary_key={"some-key": {"B": "c29tZS12YWx1ZQo="}},
                pointer_hash="cc789e5ea89c317ac58af92d7a1ba2c2",
            ),
            HashingCase(
                name="one number primary key",
                table_name="some-table",
                primary_key={"some-key": {"N": "123.456"}},
                pointer_hash="434a6dba3997ce4dbbadc98d87a0cc24",
            ),
            HashingCase(
                name="string and number primary key",
                table_name="some-table",
                primary_key={
                    "some-key": {"S": "some-value"},
                    "other-key": {"N": "123"},
                },
                pointer_hash="7aa1b80b0e49bd2078a5453399f4dd67",
            ),
            HashingCase(
                name="string and number primary key reversed",
                table_name="some-table",
                primary_key={
                    "other-key": {"N": "123"},
                    "some-key": {"S": "some-value"},
                },
                pointer_hash="7aa1b80b0e49bd2078a5453399f4dd67",
            ),
        ],
        ids=lambda case: case.name,
    )
    def test_hashing(self, hashing_case: HashingCase) -> None:
        assert (
            _aws_dynamodb_item_span_pointer_hash(
                table_name=hashing_case.table_name,
                primary_key=hashing_case.primary_key,
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
            PointersCase(
                name="s3.CopyObject",
                endpoint_name="s3",
                operation_name="CopyObject",
                request_parameters={
                    "Bucket": "some-bucket",
                    "Key": "some-key.data",
                },
                response={
                    "CopyObjectResult": {
                        "ETag": "ab12ef34",
                    },
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
                name="s3.CopyObject with double quoted ETag",
                endpoint_name="s3",
                operation_name="CopyObject",
                request_parameters={
                    "Bucket": "some-bucket",
                    "Key": "some-key.data",
                },
                response={
                    "CopyObjectResult": {
                        "ETag": '"ab12ef34"',
                    },
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
                name="s3.CompleteMultipartUpload",
                endpoint_name="s3",
                operation_name="CompleteMultipartUpload",
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
                name="s3.CompleteMultipartUpload with double quoted ETag",
                endpoint_name="s3",
                operation_name="CompleteMultipartUpload",
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
            PointersCase(
                name="dynamodb.PutItem",
                endpoint_name="dynamodb",
                operation_name="PutItem",
                request_parameters={
                    "TableName": "some-table",
                    "Item": {
                        "some-key": {"S": "some-value"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="dynamodb.PutItem with extra data",
                endpoint_name="dynamodb",
                operation_name="PutItem",
                request_parameters={
                    "TableName": "some-table",
                    "Item": {
                        "some-key": {"S": "some-value"},
                        "otehr-key": {"N": "123"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="dynamodb.PutItem unknown table",
                endpoint_name="dynamodb",
                operation_name="PutItem",
                request_parameters={
                    "TableName": "unknown-table",
                    "Item": {
                        "some-key": {"S": "some-value"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[],
                expected_warning_regex=".*unknown-table.*",
            ),
            PointersCase(
                name="dynamodb.PutItem missing primary key",
                endpoint_name="dynamodb",
                operation_name="PutItem",
                request_parameters={
                    "TableName": "some-table",
                    "Item": {
                        "other-key": {"S": "some-value"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[],
                expected_warning_regex=".*missing primary key field: some-key",
            ),
            PointersCase(
                name="dynamodb.UpdateItem",
                endpoint_name="dynamodb",
                operation_name="UpdateItem",
                request_parameters={
                    "TableName": "some-table",
                    "Key": {
                        "some-key": {"S": "some-value"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="dynamodb.UpdateItem table does not need to be known",
                endpoint_name="dynamodb",
                operation_name="UpdateItem",
                request_parameters={
                    "TableName": "unknown-table",
                    "Key": {
                        "some-key": {"S": "some-value"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="d8840182e4052ee105348b033e0a6810",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="dynamodb.UpdateItem with two key attributes",
                endpoint_name="dynamodb",
                operation_name="UpdateItem",
                request_parameters={
                    "TableName": "some-table",
                    "Key": {
                        "some-key": {"S": "some-value"},
                        "other-key": {"N": "123"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7aa1b80b0e49bd2078a5453399f4dd67",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="dynamodb.UpdateItem with three keys, impossibly",
                endpoint_name="dynamodb",
                operation_name="UpdateItem",
                request_parameters={
                    "TableName": "some-table",
                    "Key": {
                        "some-key": {"S": "some-value"},
                        "other-key": {"N": "123"},
                        "third-key-what": {"S": "some-other-value"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[],
                expected_warning_regex=".*unexpected number of primary key fields: 3",
            ),
            PointersCase(
                name="dynamodb.UpdateItem missing the key",
                endpoint_name="dynamodb",
                operation_name="UpdateItem",
                request_parameters={
                    "TableName": "some-table",
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[],
                expected_warning_regex=".*'Key'.*",
            ),
            PointersCase(
                name="dynamodb.DeleteItem",
                endpoint_name="dynamodb",
                operation_name="DeleteItem",
                request_parameters={
                    "TableName": "some-table",
                    "Key": {
                        "some-key": {"S": "some-value"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="dynamodb.DeleteItem table does not need to be known",
                endpoint_name="dynamodb",
                operation_name="DeleteItem",
                request_parameters={
                    "TableName": "unknown-table",
                    "Key": {
                        "some-key": {"S": "some-value"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="d8840182e4052ee105348b033e0a6810",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="dynamodb.DeleteItem with two key attributes",
                endpoint_name="dynamodb",
                operation_name="DeleteItem",
                request_parameters={
                    "TableName": "some-table",
                    "Key": {
                        "some-key": {"S": "some-value"},
                        "other-key": {"N": "123"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7aa1b80b0e49bd2078a5453399f4dd67",
                        extra_attributes={},
                    ),
                ],
                expected_warning_regex=None,
            ),
            PointersCase(
                name="dynamodb.DeleteItem with three keys, impossibly",
                endpoint_name="dynamodb",
                operation_name="DeleteItem",
                request_parameters={
                    "TableName": "some-table",
                    "Key": {
                        "some-key": {"S": "some-value"},
                        "other-key": {"N": "123"},
                        "third-key-what": {"S": "some-other-value"},
                    },
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[],
                expected_warning_regex=".*unexpected number of primary key fields: 3",
            ),
            PointersCase(
                name="dynamodb.DeleteItem missing the key",
                endpoint_name="dynamodb",
                operation_name="DeleteItem",
                request_parameters={
                    "TableName": "some-table",
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[],
                expected_warning_regex=".*'Key'.*",
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
                    dynamodb_primary_key_names_for_tables={
                        "some-table": {"some-key"},
                    },
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
                mock_logger.assert_called_once()

                (args, kwargs) = mock_logger.call_args
                assert not kwargs
                fmt, *other_args = args
                assert re.match(
                    pointers_case.expected_warning_regex,
                    fmt % tuple(other_args),
                )
