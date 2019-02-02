from ddtrace.encoding import get_encoder, JSONEncoder
from ddtrace.payload import Payload

from .base import BaseTestCase


class PayloadTestCase(BaseTestCase):
    def test_init(self):
        """
        When calling `Payload.init`
            With an encoder
                We use that encoder
            With no encoder
                We use the default encoder
        """
        default_encoder_type = type(get_encoder())

        payload = Payload()
        self.assertIsInstance(payload.encoder, default_encoder_type)

        json_encoder = JSONEncoder()
        payload = Payload(encoder=json_encoder)
        self.assertEqual(payload.encoder, json_encoder)

    def test_add_trace(self):
        """
        When calling `Payload.add_trace`
            With a falsey value
                Nothing is added to the payload
            With a trace
                We encode and add the trace to the payload
                We increment the payload size by the expected amount
        """
        pass
