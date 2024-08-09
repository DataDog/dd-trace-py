import aiokafka

from ddtrace.contrib.aiokafka.patch import get_version
from ddtrace.contrib.aiokafka.patch import patch
from ddtrace.contrib.aiokafka.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestAIOKafkaPatch(PatchTestCase.Base):
    __integration_name__ = "aiokafka"
    __module_name__ = "aiokafka"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, kafka):
        self.assert_wrapped(aiokafka.AIOKafkaProducer.send)
        self.assert_wrapped(aiokafka.AIOKafkaConsumer.getone)
        self.assert_wrapped(aiokafka.AIOKafkaConsumer.getmany)
        self.assert_wrapped(aiokafka.AIOKafkaConsumer.commit)

    def assert_not_module_patched(self, kafka):
        self.assert_not_wrapped(aiokafka.AIOKafkaProducer.send)
        self.assert_not_wrapped(aiokafka.AIOKafkaConsumer.getone)
        self.assert_not_wrapped(aiokafka.AIOKafkaConsumer.getmany)
        self.assert_not_wrapped(aiokafka.AIOKafkaConsumer.commit)

    def assert_not_module_double_patched(self, kafka):
        self.assert_not_double_wrapped(aiokafka.AIOKafkaProducer.send)
        self.assert_not_double_wrapped(aiokafka.AIOKafkaConsumer.getone)
        self.assert_not_double_wrapped(aiokafka.AIOKafkaConsumer.getmany)
        self.assert_not_double_wrapped(aiokafka.AIOKafkaConsumer.commit)
