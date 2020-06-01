import pynamodb.connection.base
from pynamodb.connection.base import Connection
from moto import mock_dynamodb, mock_dynamodb2
from .test import Test

# project
from ddtrace import Pin
from ddtrace.contrib.pynamodb.patch import patch, unpatch

# testing
from ...base import BaseTracerTestCase
import pdb
from moto.dynamodb import dynamodb_backend


class PynamodbTest(BaseTracerTestCase):

  TEST_SERVICE = 'pynamodb-test'

  def setUp(self):
    patch()

    self.conn = Connection(region='us-east-1')
    self.conn.session.set_credentials('aws-access-key','aws-secret-access-key','session-token')
    super(PynamodbTest, self).setUp()

  def tearDown(self):
    super(PynamodbTest, self).tearDown()
    unpatch()

  @mock_dynamodb
  def test_list_tables(self):
    dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
    Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.conn)
    self.conn.list_tables()

    spans = self.get_spans()

    assert spans
    span = spans[0]

    assert span.name == "pynamodb.command"
    assert span.service == "pynamodb-test.dynamodb"
    assert span.resource == "dynamodb.listtables"
    assert len(spans) == 1
    assert span.span_type == "http"
    assert span.get_tag("aws.operation") == "ListTables"
    assert span.get_tag("aws.region") == "us-east-1"
    assert span.get_tag("aws.agent") == "pynamodb"
    assert span.duration >= 0
    assert span.error == 0


  @mock_dynamodb
  def test_delete_table(self):
    dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
    Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.conn)
    
    self.conn.delete_table("Test")
    spans = self.get_spans()

    assert spans
    span = spans[0]

    assert span.name == "pynamodb.command"
    assert span.service == "pynamodb-test.dynamodb"
    assert span.resource == "dynamodb.deletetable"
    assert len(spans) == 1
    assert span.span_type == "http"
    assert span.get_tag("aws.operation") == "DeleteTable"
    assert span.get_tag("aws.region") == "us-east-1"
    assert span.get_tag("aws.agent") == "pynamodb"
    assert span.duration >= 0
    assert span.error == 0      

  @mock_dynamodb
  def test_scan(self):
    dynamodb_backend.create_table("Test", hash_key_attr="content", hash_key_type="S")
    
    Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(self.conn)
    self.conn.scan('Test')

    spans = self.get_spans()
    
    assert spans
    span = spans[0]

    assert span.name == "pynamodb.command"
    assert span.service == "pynamodb-test.dynamodb"
    assert span.resource == "dynamodb.scan"
    assert len(spans) == 1
    assert span.span_type == "http"
    assert span.get_tag("aws.operation") == "Scan"
    assert span.get_tag("aws.region") == "us-east-1"
    assert span.get_tag("aws.agent") == "pynamodb"
    assert span.duration >= 0
    assert span.error == 0


