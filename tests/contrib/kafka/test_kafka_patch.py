from ddtrace.contrib.kafka.patch import patch
from ddtrace.contrib.kafka.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestKafkaPatch(PatchTestCase.Base):
    __integration_name__ = "kafka"
    __module_name__ = "confluent_kafka"
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    # DEV: normally, we directly patch methods, but since confluent-kafka's methods are implemented in C and
    # directly imported, we have to patch the Producer/Consumer classes via proxy Traced Producer/Consumer classes.
    # Because of this, we need to create instances of each proxy class to make wrapping status assertions.
    def assert_module_patched(self, confluent_kafka):
        self.assert_wrapped(confluent_kafka.Producer({}))
        self.assert_wrapped(confluent_kafka.Consumer({"group.id": "group_id"}))

    def assert_not_module_patched(self, confluent_kafka):
        self.assert_not_wrapped(confluent_kafka.Producer({}))
        self.assert_not_wrapped(confluent_kafka.Consumer({"group.id": "group_id"}))

    def assert_not_module_double_patched(self, confluent_kafka):
        self.assert_not_double_wrapped(confluent_kafka.Producer({}))
        self.assert_not_double_wrapped(confluent_kafka.Consumer({"group.id": "group_id"}))
