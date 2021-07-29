from moto import mock_dynamodb
from moto.dynamodb import dynamodb_backend
import pynamodb.connection.base
from pynamodb.connection.base import Connection
import pytest

from ddtrace import Pin
from ddtrace.contrib.pynamodb.patch import patch
from ddtrace.contrib.pynamodb.patch import unpatch
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


class PynamodbTest(TracerTestCase):

    TEST_SERVICE = "pynamodb"

    def setUp(self):
        patch()

        self.conn = Connection(region="us-east-1")
        self.conn.session.set_credentials("aws-access-key", "aws-secret-access-key", "session-token")

        super(PynamodbTest, self).setUp()
        Pin.override(self.conn, tracer=self.tracer)

    def tearDown(self):
        super(PynamodbTest, self).tearDown()
        unpatch()

    @mock_dynamodb
    def test_list_tables(self):
        dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
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
        assert span.get_tag("aws.agent") == "pynamodb"
        assert span.duration >= 0
        assert span.error == 0

        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @mock_dynamodb
    def test_delete_table(self):
        dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
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
        assert span.get_tag("aws.agent") == "pynamodb"
        assert span.duration >= 0
        assert span.error == 0

        assert delete_result["Table"]["TableName"] == "Test"
        assert len(self.conn.list_tables()["TableNames"]) == 0

    @mock_dynamodb
    def test_scan(self):
        dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
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
        assert span.get_tag("aws.agent") == "pynamodb"
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
        assert span.get_tag("aws.agent") == "pynamodb"
        assert span.duration >= 0
        assert span.error == 1
        assert span.meta["error.type"] != ""

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    @mock_dynamodb
    def test_user_specified_service(self):
        from ddtrace import config

        assert config.service == "mysvc"

        dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]
        assert span.service == "pynamodb"
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_PYNAMODB_SERVICE="mypynamodb"))
    @mock_dynamodb
    def test_env_user_specified_pynamodb_service(self):
        dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
        list_result = self.conn.list_tables()

        span = self.get_spans()[0]

        assert span.service == "mypynamodb", span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

        self.reset()

        # Global config
        with self.override_config("pynamodb", dict(service="cfg-pynamodb")):
            dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
            list_result = self.conn.list_tables()
            span = self.get_spans()[0]

            assert span.service == "cfg-pynamodb", span.service
            assert len(list_result["TableNames"]) == 1
            assert list_result["TableNames"][0] == "Test"

        self.reset()

        # Manual override
        dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
        Pin.override(self.conn, service="mypynamodb", tracer=self.tracer)
        list_result = self.conn.list_tables()
        span = self.get_spans()[0]
        assert span.service == "mypynamodb", span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="app-svc", DD_PYNAMODB_SERVICE="env-pynamodb"))
    @mock_dynamodb
    def test_service_precedence(self):
        dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
        list_result = self.conn.list_tables()
        span = self.get_spans()[0]
        assert span.service == "env-pynamodb", span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"

        self.reset()

        # Manual override
        dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
        Pin.override(self.conn, service="override-pynamodb", tracer=self.tracer)
        list_result = self.conn.list_tables()
        span = self.get_spans()[0]
        assert span.service == "override-pynamodb", span.service
        assert len(list_result["TableNames"]) == 1
        assert list_result["TableNames"][0] == "Test"
