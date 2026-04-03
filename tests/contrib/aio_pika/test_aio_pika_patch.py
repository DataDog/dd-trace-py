from ddtrace.contrib.internal.aio_pika.patch import get_version
from ddtrace.contrib.internal.aio_pika.patch import patch
from ddtrace.contrib.internal.aio_pika.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestAioPikaPatch(PatchTestCase.Base):
    __integration_name__ = "aio_pika"
    __module_name__ = "aio_pika"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, aio_pika):
        self.assert_wrapped(aio_pika.Exchange.publish)
        self.assert_wrapped(aio_pika.queue.consumer)
        self.assert_wrapped(aio_pika.Queue.get)
        self.assert_wrapped(aio_pika.IncomingMessage.ack)
        self.assert_wrapped(aio_pika.IncomingMessage.nack)
        self.assert_wrapped(aio_pika.IncomingMessage.reject)

    def assert_not_module_patched(self, aio_pika):
        self.assert_not_wrapped(aio_pika.Exchange.publish)
        self.assert_not_wrapped(aio_pika.queue.consumer)
        self.assert_not_wrapped(aio_pika.Queue.get)
        self.assert_not_wrapped(aio_pika.IncomingMessage.ack)
        self.assert_not_wrapped(aio_pika.IncomingMessage.nack)
        self.assert_not_wrapped(aio_pika.IncomingMessage.reject)

    def assert_not_module_double_patched(self, aio_pika):
        self.assert_not_double_wrapped(aio_pika.Exchange.publish)
        self.assert_not_double_wrapped(aio_pika.queue.consumer)
        self.assert_not_double_wrapped(aio_pika.Queue.get)
        self.assert_not_double_wrapped(aio_pika.IncomingMessage.ack)
        self.assert_not_double_wrapped(aio_pika.IncomingMessage.nack)
        self.assert_not_double_wrapped(aio_pika.IncomingMessage.reject)
