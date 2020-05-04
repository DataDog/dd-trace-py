import pynamodb.connection.base
from moto import mock_dynamodb, mock_dynamodb2

# project
from ddtrace import Pin
from ddtrace.contrib.pynamodb.patch import patch, unpatch

class PynamodbTest(BaseTracerTestCase):

  def setUp(self):
    patch()

    self.session = pynamodb.connection.base.session()
    self.session.set_credentials('aws-access-key','aws-secret-access-key','session-token')
    super(PynamodbTest, self).setUp()

  def tearDown(self)
    super(PynamodbTest, self).tearDown()
    unpatch()