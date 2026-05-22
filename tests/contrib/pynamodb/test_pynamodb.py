import boto3
from moto import mock_dynamodb
import pynamodb.connection.base
from pynamodb.connection.base import Connection
import pytest

from ddtrace.contrib.internal.pynamodb.patch import patch
from ddtrace.contrib.internal.pynamodb.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


import moto as _moto

if int(_moto.__version__.split(".")[0]) < 4:
    # moto 1.x: direct backend API (no HTTP; compatible with pynamodb 5.x)
    from moto.dynamodb import dynamodb_backend as _moto_dynamodb_backend

    def _create_table(name: str = "Test") -> None:
        _moto_dynamodb_backend.create_table(name, hash_key_attr="content", hash_key_type="S")

else:
    # moto 4.x+: use boto3 (compatible with pynamodb 6.x / botocore-backed)
    def _create_table(name: str = "Test") -> None:  # type: ignore[misc]
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName=name,
            KeySchema=[{"AttributeName": "content", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "content", "AttributeType": "S"}],
            ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
        )


class PynamodbTest(TracerTestCase):
    def setUp(self):
        patch()
        # Pass credentials via constructor to defer botocore session creation
        # until the first actual API call (inside the @mock_dynamodb context).
        self.conn = Connection(
            region="us-east-1",
            aws_access_key_id="aws-access-key",
            aws_secret_access_key="aws-secret-access-key",
            aws_session_token="session-token",
        )
        super(PynamodbTest, self).setUp()

    def tearDown(self):
        super(PynamodbTest, self).tearDown()
        unpatch()

    @mock_dynamodb
    def test_list_tables(self):
        _create_table()
        list_result = self.conn.list_tables()

        spans = self.get_spans()

        assert spans
        span = spans[0]

        assert span.name == "pynamodb.command"
        assert span.service == "pynamodb"
        assert span.resource == "ListTables"
        assert len(spans) == 1
        assert_is_measured(span)
        assert span.span_type == "http"
        assert span.get_tag("aws.operation") == "ListTables"
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.agent") == "pynamodb"
        assert span.get_tag("component") == "pynamodb"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "dynamodb"
        assert span.duration >= 0
        assert span.error == 0

        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @mock_dynamodb
    def test_delete_table(self):
        _create_table()
        delete_result = self.conn.delete_table("Test")
        spans = self.get_spans()

        assert spans
        span = spans[0]

        assert span.name == "pynamodb.command"
        assert span.service == "pynamodb"
        assert span.resource == "DeleteTable Test"
        assert len(spans) == 1
        assert_is_measured(span)
        assert span.span_type == "http"
        assert span.get_tag("aws.operation") == "DeleteTable"
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.agent") == "pynamodb"
        assert span.get_tag("component") == "pynamodb"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "dynamodb"
        assert span.duration >= 0
        assert span.error == 0

        # pynamodb 5.x (raw JSON) uses "Table", pynamodb 6.x (botocore-parsed) uses "TableDescription"
        table_desc = delete_result.get("TableDescription") or delete_result.get("Table", {})
        assert table_desc.get("TableName") == "Test"
        assert len(self.conn.list_tables()["TableNames"]) == 0

    @mock_dynamodb
    def test_scan(self):
        _create_table()
        scan_result = self.conn.scan("Test")
        spans = self.get_spans()

        assert spans
        span = spans[0]

        assert span.name == "pynamodb.command"
        assert span.service == "pynamodb"
        assert span.resource == "Scan Test"
        assert len(spans) == 1
        assert_is_measured(span)
        assert span.span_type == "http"
        assert span.get_tag("aws.operation") == "Scan"
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.agent") == "pynamodb"
        assert span.get_tag("component") == "pynamodb"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "dynamodb"
        assert span.duration >= 0
        assert span.error == 0

        assert scan_result["ScannedCount"] == 0
        assert len(scan_result["Items"]) == 0

    @mock_dynamodb
    def test_scan_on_error(self):
        with pytest.raises(pynamodb.exceptions.ScanError):
            self.conn.scan("OtherTable")

        spans = self.get_spans()
        assert spans
        span = spans[0]
        assert span.name == "pynamodb.command"
        assert span.service == "pynamodb"
        assert span.resource == "Scan OtherTable"
        assert len(spans) == 1
        assert_is_measured(span)
        assert span.span_type == "http"
        assert span.get_tag("aws.operation") == "Scan"
        assert span.get_tag("aws.region") == "us-east-1"
        assert span.get_tag("region") == "us-east-1"
        assert span.get_tag("aws.agent") == "pynamodb"
        assert span.get_tag("component") == "pynamodb"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "dynamodb"
        assert span.duration >= 0
        assert span.error == 1
        assert span.get_tag("error.type") != ""

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    @mock_dynamodb
    def test_schematized_service_default(self):
        from ddtrace import config

        assert config.service == "mysvc"

        _create_table()
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]
        assert span.service == "pynamodb", "Expected 'pynamodb', got %s" % span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    @mock_dynamodb
    def test_schematized_service_v0(self):
        from ddtrace import config

        assert config.service == "mysvc"

        _create_table()
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]
        assert span.service == "pynamodb", "Expected 'pynamodb', got %s" % span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    @mock_dynamodb
    def test_schematized_service_v1(self):
        from ddtrace import config

        assert config.service == "mysvc"

        _create_table()
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]
        assert span.service == "mysvc", "Expected 'mysvc', got %s" % span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    @mock_dynamodb
    def test_schematized_unspecified_service_default(self):
        _create_table()
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]
        assert span.service == "pynamodb", "Expected 'pynamodb', got %s" % span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    @mock_dynamodb
    def test_schematized_unspecified_service_v0(self):
        _create_table()
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]
        assert span.service == "pynamodb", "Expected 'pynamodb', got %s" % span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    @mock_dynamodb
    def test_schematized_unspecified_service_v1(self):
        _create_table()
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME, (
            "Expected 'internal.schema.DEFAULT_SEVICE_NAME', got %s" % span.service
        )
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    @mock_dynamodb
    def test_schematized_operation_v0(self):
        _create_table()
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]
        assert span.name == "pynamodb.command", "Expected 'pynamodb.command', got %s" % span.name
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    @mock_dynamodb
    def test_schematized_operation_v1(self):
        _create_table()
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]
        assert span.name == "aws.dynamodb.request", "Expected 'aws.dynamodb.request', got %s" % span.name
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_PYNAMODB_SERVICE="mypynamodb"))
    @mock_dynamodb
    def test_env_user_specified_pynamodb_service(self):
        _create_table()
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]

        assert span.service == "mypynamodb", span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

        self.reset()

        # Global config - table "Test" still exists from the first block above
        with self.override_config("pynamodb", dict(service="cfg-pynamodb")):
            list_result = self.conn.list_tables()
            span = self.get_spans()[0]

            assert span.service == "cfg-pynamodb", span.service
            assert len(list_result["TableNames"]) == 1
            assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="app-svc", DD_PYNAMODB_SERVICE="env-pynamodb"))
    @mock_dynamodb
    def test_service_precedence(self):
        _create_table()
        list_result = self.conn.list_tables()
        span = self.get_spans()[0]
        assert span.service == "env-pynamodb", span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"
