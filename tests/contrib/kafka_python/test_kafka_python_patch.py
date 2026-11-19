from ddtrace.contrib.internal.kafka_python.patch import get_version
from ddtrace.contrib.internal.kafka_python.patch import patch
from ddtrace.contrib.internal.kafka_python.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestKafkaPythonPatch(PatchTestCase.Base):
    __integration_name__ = "kafka_python"
    __module_name__ = "kafka"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, kafka):
        self.assert_wrapped(kafka.KafkaProducer.send)
        self.assert_wrapped(kafka.KafkaConsumer.__next__)
        self.assert_wrapped(kafka.KafkaConsumer.poll)
        self.assert_wrapped(kafka.coordinator.consumer.ConsumerCoordinator.commit_offsets_sync)
        self.assert_wrapped(kafka.coordinator.consumer.ConsumerCoordinator.commit_offsets_async)

    def assert_not_module_patched(self, kafka):
        self.assert_not_wrapped(kafka.KafkaProducer.send)
        self.assert_not_wrapped(kafka.KafkaConsumer.__next__)
        self.assert_not_wrapped(kafka.KafkaConsumer.poll)
        self.assert_not_wrapped(kafka.coordinator.consumer.ConsumerCoordinator.commit_offsets_sync)
        self.assert_not_wrapped(kafka.coordinator.consumer.ConsumerCoordinator.commit_offsets_async)

    def assert_not_module_double_patched(self, kafka):
        self.assert_not_double_wrapped(kafka.KafkaProducer.send)
        self.assert_not_double_wrapped(kafka.KafkaConsumer.__next__)
        self.assert_not_double_wrapped(kafka.KafkaConsumer.poll)
        self.assert_not_double_wrapped(kafka.coordinator.consumer.ConsumerCoordinator.commit_offsets_sync)
        self.assert_not_double_wrapped(kafka.coordinator.consumer.ConsumerCoordinator.commit_offsets_async)
