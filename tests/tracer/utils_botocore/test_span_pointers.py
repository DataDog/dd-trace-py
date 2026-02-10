from decimal import Decimal
import logging
import re
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Set
from typing import Union

import mock
import pytest

from ddtrace._trace._span_pointer import _SpanPointerDescription
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace._trace.utils_botocore.span_pointers import extract_span_pointers_from_successful_botocore_response
from ddtrace._trace.utils_botocore.span_pointers.dynamodb import _aws_dynamodb_item_primary_key_from_write_request
from ddtrace._trace.utils_botocore.span_pointers.dynamodb import _aws_dynamodb_item_span_pointer_hash
from ddtrace._trace.utils_botocore.span_pointers.dynamodb import _DynamoDBTableName
from ddtrace._trace.utils_botocore.span_pointers.dynamodb import _DynamoDBWriteRequest
from ddtrace._trace.utils_botocore.span_pointers.dynamodb import _identify_dynamodb_batch_write_item_processed_items
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
                operation="SomeOperation",
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
        primary_key: Dict[str, Union[Dict[str, str], str, Decimal, bytes]]
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
                name="one string primary key deserializd",
                table_name="some-table",
                primary_key={"some-key": "some-value"},
                pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
            ),
            HashingCase(
                name="one binary primary key",
                table_name="some-table",
                primary_key={"some-key": {"B": "c29tZS12YWx1ZQo="}},
                pointer_hash="cc789e5ea89c317ac58af92d7a1ba2c2",
            ),
            HashingCase(
                name="one binary primary key deserialized",
                table_name="some-table",
                primary_key={"some-key": b"c29tZS12YWx1ZQo="},
                pointer_hash="cc789e5ea89c317ac58af92d7a1ba2c2",
            ),
            HashingCase(
                name="one number primary key",
                table_name="some-table",
                primary_key={"some-key": {"N": "123.456"}},
                pointer_hash="434a6dba3997ce4dbbadc98d87a0cc24",
            ),
            HashingCase(
                name="one number primary key deserialized",
                table_name="some-table",
                primary_key={"some-key": Decimal("123.456")},
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
                name="string and number primary key deserialized",
                table_name="some-table",
                primary_key={
                    "some-key": "some-value",
                    "other-key": Decimal("123"),
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
                operation="SomeOperation",
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
        expected_logger_regex: Optional[str]

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
                expected_logger_regex=None,
            ),
            PointersCase(
                name="unknown s3 operation",
                endpoint_name="s3",
                operation_name="unknown",
                request_parameters={},
                response={},
                expected_pointers=[],
                expected_logger_regex=None,
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
                expected_logger_regex=r"span pointers: problem with parameters for S3.PutObject .*: 'Bucket'",
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
                expected_logger_regex=r"span pointers: problem with parameters for S3.PutObject .*: 'Key'",
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
                expected_logger_regex=r"span pointers: problem with parameters for S3.PutObject .*: 'ETag'",
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
                expected_logger_regex=r".*'ascii' codec can't encode characters.*",
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
            ),
            PointersCase(
                name="dynamodb.PutItem deserialized",
                endpoint_name="dynamodb",
                operation_name="PutItem",
                request_parameters={
                    "TableName": "some-table",
                    "Item": {
                        "some-key": "some-value",
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=".*unknown-table.*",
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
                expected_logger_regex=".*missing primary key field: some-key",
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
                expected_logger_regex=None,
            ),
            PointersCase(
                name="dynamodb.UpdateItem deserialized",
                endpoint_name="dynamodb",
                operation_name="UpdateItem",
                request_parameters={
                    "TableName": "some-table",
                    "Key": {
                        "some-key": "some-value",
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=".*unexpected number of primary key fields: 3",
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
                expected_logger_regex=".*'Key'.*",
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
                expected_logger_regex=None,
            ),
            PointersCase(
                name="dynamodb.DeleteItem deserialized",
                endpoint_name="dynamodb",
                operation_name="DeleteItem",
                request_parameters={
                    "TableName": "some-table",
                    "Key": {
                        "some-key": "some-value",
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=None,
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
                expected_logger_regex=".*unexpected number of primary key fields: 3",
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
                expected_logger_regex=".*'Key'.*",
            ),
            PointersCase(
                name="dynamodb.BatchWriteItem works with multiple items and tables",
                endpoint_name="dynamodb",
                operation_name="BatchWriteItem",
                request_parameters={
                    "RequestItems": {
                        "some-table": [
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": {"S": "some-value"},
                                    },
                                },
                            },
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": {"S": "will-not-complete"},
                                    },
                                },
                            },
                        ],
                        "unknown-table": [
                            {
                                "DeleteRequest": {
                                    "Key": {
                                        "some-key": {"S": "some-value"},
                                    },
                                },
                            },
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": {"S": "will-also-not-complete"},
                                    },
                                },
                            },
                        ],
                    },
                },
                response={
                    "UnprocessedItems": {
                        "some-table": [
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": {"S": "will-not-complete"},
                                    },
                                },
                            },
                        ],
                        "unknown-table": [
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": {"S": "will-also-not-complete"},
                                    },
                                },
                            },
                        ],
                    },
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
                        extra_attributes={},
                    ),
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="d8840182e4052ee105348b033e0a6810",
                        extra_attributes={},
                    ),
                ],
                expected_logger_regex=None,
            ),
            PointersCase(
                name="dynamodb.BatchWriteItem works with multiple items and tables serialized",
                endpoint_name="dynamodb",
                operation_name="BatchWriteItem",
                request_parameters={
                    "RequestItems": {
                        "some-table": [
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": "some-value",
                                    },
                                },
                            },
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": "will-not-complete",
                                    },
                                },
                            },
                        ],
                        "unknown-table": [
                            {
                                "DeleteRequest": {
                                    "Key": {
                                        "some-key": "some-value",
                                    },
                                },
                            },
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": "will-also-not-complete",
                                    },
                                },
                            },
                        ],
                    },
                },
                response={
                    "UnprocessedItems": {
                        "some-table": [
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": "will-not-complete",
                                    },
                                },
                            },
                        ],
                        "unknown-table": [
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": "will-also-not-complete",
                                    },
                                },
                            },
                        ],
                    },
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
                        extra_attributes={},
                    ),
                    _SpanPointerDescription(
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="d8840182e4052ee105348b033e0a6810",
                        extra_attributes={},
                    ),
                ],
                expected_logger_regex=None,
            ),
            PointersCase(
                name="dynamodb.BatchWriteItem still needs the mapping sometimes",
                endpoint_name="dynamodb",
                operation_name="BatchWriteItem",
                request_parameters={
                    "RequestItems": {
                        "unknown-table": [
                            {
                                "PutRequest": {
                                    "Item": {
                                        "some-key": {"S": "some-value"},
                                    },
                                },
                            },
                        ],
                    },
                },
                response={},
                expected_pointers=[],
                expected_logger_regex=".*unknown-table.*",
            ),
            PointersCase(
                name="dynamodb.TransactWriteItems basic case",
                endpoint_name="dynamodb",
                operation_name="TransactWriteItems",
                request_parameters={
                    "TransactItems": [
                        {
                            "Put": {
                                "TableName": "some-table",
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                        {
                            "Delete": {
                                "TableName": "unknown-table",
                                "Key": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                        {
                            "Update": {
                                "TableName": "some-table",
                                "Key": {
                                    "some-key": {"S": "some-value"},
                                    "other-key": {"N": "123"},
                                },
                            },
                        },
                        {
                            "ConditionCheck": {
                                "TableName": "do-not-care-table",
                                "Key": {
                                    "do-not-care-key": {"S": "meh"},
                                },
                            },
                        },
                    ],
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        # Update
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7aa1b80b0e49bd2078a5453399f4dd67",
                        extra_attributes={},
                    ),
                    _SpanPointerDescription(
                        # Put
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
                        extra_attributes={},
                    ),
                    _SpanPointerDescription(
                        # Delete
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="d8840182e4052ee105348b033e0a6810",
                        extra_attributes={},
                    ),
                ],
                expected_logger_regex=None,
            ),
            PointersCase(
                name="dynamodb.TransactWriteItems basic case deserialized",
                endpoint_name="dynamodb",
                operation_name="TransactWriteItems",
                request_parameters={
                    "TransactItems": [
                        {
                            "Put": {
                                "TableName": "some-table",
                                "Item": {
                                    "some-key": "some-value",
                                },
                            },
                        },
                        {
                            "Delete": {
                                "TableName": "unknown-table",
                                "Key": {
                                    "some-key": "some-value",
                                },
                            },
                        },
                        {
                            "Update": {
                                "TableName": "some-table",
                                "Key": {
                                    "some-key": "some-value",
                                    "other-key": 123,
                                },
                            },
                        },
                        {
                            "ConditionCheck": {
                                "TableName": "do-not-care-table",
                                "Key": {
                                    "do-not-care-key": "meh",
                                },
                            },
                        },
                    ],
                },
                response={
                    # things we do not care about
                },
                expected_pointers=[
                    _SpanPointerDescription(
                        # Update
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7aa1b80b0e49bd2078a5453399f4dd67",
                        extra_attributes={},
                    ),
                    _SpanPointerDescription(
                        # Put
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="7f1aee721472bcb48701d45c7c7f7821",
                        extra_attributes={},
                    ),
                    _SpanPointerDescription(
                        # Delete
                        pointer_kind="aws.dynamodb.item",
                        pointer_direction=_SpanPointerDirection.DOWNSTREAM,
                        pointer_hash="d8840182e4052ee105348b033e0a6810",
                        extra_attributes={},
                    ),
                ],
                expected_logger_regex=None,
            ),
            PointersCase(
                name="dynamodb.TransactWriteItems still needs the mapping sometimes",
                endpoint_name="dynamodb",
                operation_name="TransactWriteItems",
                request_parameters={
                    "TransactItems": [
                        {
                            "Put": {
                                "TableName": "unknown-table",
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                response={},
                expected_pointers=[],
                expected_logger_regex=".*unknown-table.*",
            ),
        ],
        ids=lambda case: case.name,
    )
    def test_pointers(self, pointers_case: PointersCase) -> None:
        # We might like to use caplog here but it resulted in inconsistent test
        # behavior, so we have to go a bit deeper.

        with mock.patch.object(logging.Logger, "debug") as mock_logger:
            assert sorted(
                extract_span_pointers_from_successful_botocore_response(
                    dynamodb_primary_key_names_for_tables={
                        "some-table": {"some-key"},
                    },
                    endpoint_name=pointers_case.endpoint_name,
                    operation_name=pointers_case.operation_name,
                    request_parameters=pointers_case.request_parameters,
                    response=pointers_case.response,
                ),
                key=lambda pointer: pointer.pointer_hash,
            ) == sorted(pointers_case.expected_pointers, key=lambda pointer: pointer.pointer_hash)

            span_pointer_log_args = [
                call_args for call_args in mock_logger.call_args_list if call_args[0][0].startswith("span pointers: ")
            ]
            if pointers_case.expected_logger_regex is None:
                assert not span_pointer_log_args

            else:
                assert len(span_pointer_log_args) == 1

                (args, kwargs) = span_pointer_log_args.pop()
                assert not kwargs
                fmt, *other_args = args
                assert re.match(
                    pointers_case.expected_logger_regex,
                    fmt % tuple(other_args),
                )


class TestDynamoDBWriteRequestLogic:
    class WriteRequestPrimaryKeyCase(NamedTuple):
        name: str
        table_name: str
        write_request: _DynamoDBWriteRequest
        primary_key: Optional[Dict[str, Dict[str, str]]]
        expected_logger_regex: Optional[str]

    @pytest.mark.parametrize(
        "test_case",
        [
            WriteRequestPrimaryKeyCase(
                name="put request",
                table_name="some-table",
                write_request={
                    "PutRequest": {
                        "Item": {
                            "some-key": {"S": "some-value"},
                            "extra-data": {"N": "123"},
                        },
                    },
                },
                primary_key={"some-key": {"S": "some-value"}},
                expected_logger_regex=None,
            ),
            WriteRequestPrimaryKeyCase(
                name="delete request",
                table_name="unknown-table",
                write_request={
                    "DeleteRequest": {
                        "Key": {
                            "some-key": {"S": "some-value"},
                        },
                    },
                },
                primary_key={"some-key": {"S": "some-value"}},
                expected_logger_regex=None,
            ),
            WriteRequestPrimaryKeyCase(
                name="impossible combined request",
                table_name="unknown-table",
                write_request={
                    "PutRequest": {
                        "Item": {
                            "some-key": {"S": "some-value"},
                            "extra-data": {"N": "123"},
                        },
                    },
                    "DeleteRequest": {
                        "Key": {
                            "some-key": {"S": "some-value"},
                        },
                    },
                },
                primary_key=None,
                expected_logger_regex="span pointers: unexpected number of write request fields",
            ),
            WriteRequestPrimaryKeyCase(
                name="unknown request kind",
                table_name="some-table",
                write_request={
                    "SomeRequest": {
                        "Item": {
                            "some-key": {"S": "some-value"},
                            "extra-data": {"N": "123"},
                        },
                    },
                },
                primary_key=None,
                expected_logger_regex="span pointers: unexpected write request structure: SomeRequest",
            ),
        ],
        ids=lambda test_case: test_case.name,
    )
    def test_aws_dynamodb_item_primary_key_from_write_request(self, test_case: WriteRequestPrimaryKeyCase) -> None:
        with mock.patch.object(logging.Logger, "debug") as mock_logger:
            assert (
                _aws_dynamodb_item_primary_key_from_write_request(
                    dynamodb_primary_key_names_for_tables={
                        "some-table": {"some-key"},
                    },
                    table_name=test_case.table_name,
                    write_request=test_case.write_request,
                )
                == test_case.primary_key
            )

            span_pointer_log_args = [
                call_args for call_args in mock_logger.call_args_list if call_args[0][0].startswith("span pointers: ")
            ]
            if test_case.expected_logger_regex is None:
                assert not span_pointer_log_args

            else:
                assert len(span_pointer_log_args) == 1

                (args, kwargs) = span_pointer_log_args.pop()
                assert not kwargs
                fmt, *other_args = args
                assert re.match(
                    test_case.expected_logger_regex,
                    fmt % tuple(other_args),
                )

    class ProcessedWriteRequestCase(NamedTuple):
        name: str
        requested_items: Dict[_DynamoDBTableName, List[_DynamoDBWriteRequest]]
        unprocessed_items: Dict[_DynamoDBTableName, List[_DynamoDBWriteRequest]]
        expected_processed_items: Optional[Dict[_DynamoDBTableName, List[_DynamoDBWriteRequest]]]
        expected_logger_regex: Optional[str]

    @pytest.mark.parametrize(
        "test_case",
        [
            ProcessedWriteRequestCase(
                name="nothing unprocessed",
                requested_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                unprocessed_items={},
                expected_processed_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                expected_logger_regex=None,
            ),
            ProcessedWriteRequestCase(
                name="all unprocessed",
                requested_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                unprocessed_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                expected_processed_items={},
                expected_logger_regex=None,
            ),
            ProcessedWriteRequestCase(
                name="some unprocessed",
                requested_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                    "other-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                unprocessed_items={
                    "other-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                expected_processed_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                expected_logger_regex=None,
            ),
            ProcessedWriteRequestCase(
                name="nothing unprocessed",
                requested_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                unprocessed_items={},
                expected_processed_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                expected_logger_regex=None,
            ),
            ProcessedWriteRequestCase(
                name="extra unprocessed tables",
                requested_items={},
                unprocessed_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                expected_processed_items=None,
                expected_logger_regex=".*unprocessed items include tables not in the requested items",
            ),
            ProcessedWriteRequestCase(
                name="extra unprocessed items",
                requested_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                    ],
                },
                unprocessed_items={
                    "some-table": [
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "some-value"},
                                },
                            },
                        },
                        {
                            "PutRequest": {
                                "Item": {
                                    "some-key": {"S": "other-value"},
                                },
                            },
                        },
                    ],
                },
                expected_processed_items=None,
                expected_logger_regex=".*unprocessed write requests include items not in the requested write requests",
            ),
        ],
        ids=lambda test_case: test_case.name,
    )
    def test_identify_dynamodb_batch_write_item_processed_items(self, test_case: ProcessedWriteRequestCase) -> None:
        with mock.patch.object(logging.Logger, "debug") as mock_logger:
            processed_items = _identify_dynamodb_batch_write_item_processed_items(
                requested_items=test_case.requested_items,
                unprocessed_items=test_case.unprocessed_items,
            )
            assert processed_items == test_case.expected_processed_items

            span_pointer_log_args = [
                call_args for call_args in mock_logger.call_args_list if call_args[0][0].startswith("span pointers: ")
            ]

            if test_case.expected_logger_regex is None:
                assert not span_pointer_log_args

            else:
                assert len(span_pointer_log_args) == 1

                (args, kwargs) = span_pointer_log_args.pop()
                assert not kwargs
                fmt, *other_args = args
                assert re.match(
                    test_case.expected_logger_regex,
                    fmt % tuple(other_args),
                )

                return

        def collect_all_ids(thing: object, accumulator: Set[int]) -> None:
            if isinstance(thing, dict):
                accumulator.add(id(thing))
                for value in thing.values():
                    collect_all_ids(value, accumulator)

            elif isinstance(thing, list):
                accumulator.add(id(thing))
                for item in thing:
                    collect_all_ids(item, accumulator)

            elif isinstance(thing, str):
                # These can be reused internally, but that's fine since they
                # are immutable.
                pass

            else:
                raise ValueError(f"unknown type of thing: {type(thing)}")

        processed_items_ids: Set[int] = set()
        collect_all_ids(processed_items, processed_items_ids)

        expected_processed_items_ids: Set[int] = set()
        collect_all_ids(test_case.expected_processed_items, expected_processed_items_ids)

        assert not (processed_items_ids & expected_processed_items_ids), "the objects should be distinct"
