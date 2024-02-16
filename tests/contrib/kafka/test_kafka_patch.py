from ddtrace.contrib.kafka.patch import get_version
from ddtrace.contrib.kafka.patch import patch
from ddtrace.contrib.kafka.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestKafkaPatch(PatchTestCase.Base):
    __integration_name__ = "kafka"
    __module_name__ = "confluent_kafka"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, confluent_kafka):
        self.assert_wrapped(confluent_kafka.Producer({}).produce)
        self.assert_wrapped(confluent_kafka.Consumer({"group.id": "group_id"}).poll)
        self.assert_wrapped(confluent_kafka.SerializingProducer({}).produce)
        self.assert_wrapped(confluent_kafka.DeserializingConsumer({"group.id": "group_id"}).poll)

    def assert_not_module_patched(self, confluent_kafka):
        self.assert_not_wrapped(confluent_kafka.Producer({}).produce)
        self.assert_not_wrapped(confluent_kafka.Consumer({"group.id": "group_id"}).poll)
        self.assert_not_wrapped(confluent_kafka.SerializingProducer({}).produce)
        self.assert_not_wrapped(confluent_kafka.DeserializingConsumer({"group.id": "group_id"}).poll)

    def assert_not_module_double_patched(self, confluent_kafka):
        self.assert_not_double_wrapped(confluent_kafka.Producer({}).produce)
        self.assert_not_double_wrapped(confluent_kafka.Consumer({"group.id": "group_id"}).poll)
        self.assert_not_double_wrapped(confluent_kafka.SerializingProducer({}).produce)
        self.assert_not_double_wrapped(confluent_kafka.DeserializingConsumer({"group.id": "group_id"}).poll)
