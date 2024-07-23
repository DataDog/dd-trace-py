from ddtrace.contrib.kafka_python_ng.patch import get_version
from ddtrace.contrib.kafka_python_ng.patch import patch
from ddtrace.contrib.kafka_python_ng.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestKafkaPythonNgPatch(PatchTestCase.Base):
    __integration_name__ = "kafka_python_ng"
    __module_name__ = "kafka"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, kafka):
        self.assert_wrapped(kafka.KafkaProducer({}).produce)
        self.assert_wrapped(kafka.KafkaConsumer({"group.id": "group_id"}).poll)

    def assert_not_module_patched(self, kafka):
        self.assert_not_wrapped(kafka.KafkaProducer({}).produce)
        self.assert_not_wrapped(kafka.KafkaConsumer({"group.id": "group_id"}).poll)

    def assert_not_module_double_patched(self, kafka):
        self.assert_not_double_wrapped(kafka.KafkaProducer({}).produce)
        self.assert_not_double_wrapped(kafka.KafkaConsumer({"group.id": "group_id"}).poll)
