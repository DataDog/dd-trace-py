import pynamodb.connection.base
from moto import mock_dynamodb, mock_dynamodb2

# project
from ddtrace import Pin
from ddtrace.contrib.pynamodb.patch import patch, unpatch

# testing
from ...base import BaseTracerTestCase

class PynamodbTest(BaseTracerTestCase):

  TEST_SERVICE = 'pynamodb-test'

  def setUp(self):
    patch()

    self.session = pynamodb.connection.base.get_session()
    self.session.set_credentials('aws-access-key','aws-secret-access-key','session-token')
    super(PynamodbTest, self).setUp()

  def tearDown(self):
    super(PynamodbTest, self).tearDown()
    unpatch()

  @mock_dynamodb
  def test_traced_client(self):
    dynamodb = self.session.create_client('dynamodb', region_name='us-east-1')
    Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(dynamodb)

