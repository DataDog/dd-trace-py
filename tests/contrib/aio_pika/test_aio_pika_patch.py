import aio_pika

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

    def _targets(self, module):
        targets = [
            module.exchange.Exchange.publish,
            module.queue.consumer,
            module.queue.Queue.get,
            module.queue.QueueIterator.__anext__,
            module.message.IncomingMessage.process,
            module.message.IncomingMessage.ack,
            module.message.IncomingMessage.nack,
            module.message.IncomingMessage.reject,
        ]
        if "__anext__" in module.robust_queue.RobustQueueIterator.__dict__:
            targets.append(module.robust_queue.RobustQueueIterator.__anext__)
        return targets

    def assert_module_patched(self, aio_pika):
        for target in self._targets(aio_pika):
            self.assert_wrapped(target)

    def assert_not_module_patched(self, aio_pika):
        for target in self._targets(aio_pika):
            self.assert_not_wrapped(target)

    def assert_not_module_double_patched(self, aio_pika):
        for target in self._targets(aio_pika):
            self.assert_not_double_wrapped(target)


def teardown_module():
    unpatch()
    assert not getattr(aio_pika, "_datadog_patch", False)
