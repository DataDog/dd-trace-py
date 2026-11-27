from ddtrace.contrib.internal.pika.patch import get_version
from ddtrace.contrib.internal.pika.patch import patch
from ddtrace.contrib.internal.pika.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestPikaPatch(PatchTestCase.Base):
    __integration_name__ = "pika"
    __module_name__ = "pika"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def _targets(self, pika):
        from pika.adapters.blocking_connection import BlockingChannel

        return (
            pika.channel.Channel.basic_publish,
            pika.channel.Channel.basic_consume,
            pika.channel.Channel.basic_get,
            BlockingChannel.basic_consume,
            BlockingChannel.consume,
        )

    def assert_module_patched(self, pika):
        for target in self._targets(pika):
            self.assert_wrapped(target)

    def assert_not_module_patched(self, pika):
        for target in self._targets(pika):
            self.assert_not_wrapped(target)

    def assert_not_module_double_patched(self, pika):
        for target in self._targets(pika):
            self.assert_not_double_wrapped(target)
