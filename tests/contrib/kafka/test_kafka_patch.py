from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import confluent_kafka
import pytest

from ddtrace.contrib.internal.kafka.patch import TracedConsumer
from ddtrace.contrib.internal.kafka.patch import TracedProducer
from ddtrace.contrib.internal.kafka.patch import _cluster_id_by_bootstrap
from ddtrace.contrib.internal.kafka.patch import _get_cluster_id
from ddtrace.contrib.internal.kafka.patch import get_version
from ddtrace.contrib.internal.kafka.patch import patch
from ddtrace.contrib.internal.kafka.patch import unpatch
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


@pytest.fixture(autouse=True)
def _clear_cluster_id_cache():
    """Reset the process-wide cluster ID cache between tests."""
    _cluster_id_by_bootstrap.clear()
    yield
    _cluster_id_by_bootstrap.clear()


class TestGetClusterId:
    def _make_consumer(self, bootstrap_servers: str = "localhost:9092") -> TracedConsumer:
        patch()
        consumer = confluent_kafka.Consumer({"bootstrap.servers": bootstrap_servers, "group.id": "test_group"})
        return consumer

    def _make_producer(self, bootstrap_servers: str = "localhost:9092") -> TracedProducer:
        patch()
        producer = confluent_kafka.Producer({"bootstrap.servers": bootstrap_servers})
        return producer

    def teardown_method(self) -> None:
        unpatch()

    def test_consumer_returns_empty_string(self) -> None:
        """Consumer instances must return '' without calling list_topics."""
        consumer = self._make_consumer()
        with mock_patch.object(type(consumer), "list_topics") as mock_list_topics:
            result = _get_cluster_id(consumer, "test-topic")
        assert result == ""
        mock_list_topics.assert_not_called()

    def test_consumer_never_calls_list_topics(self) -> None:
        """list_topics must never be invoked on a Consumer regardless of cache state."""
        consumer = self._make_consumer()
        consumer.list_topics = MagicMock()  # type: ignore[method-assign]
        _get_cluster_id(consumer, "test-topic")
        consumer.list_topics.assert_not_called()

    def test_consumer_uses_producer_populated_cache(self) -> None:
        """Consumer reads cluster ID from the process-wide cache set by a producer."""
        bootstrap = "localhost:9092"
        producer = self._make_producer(bootstrap)
        mock_metadata = MagicMock()
        mock_metadata.cluster_id = "test-cluster-123"
        with mock_patch.object(type(producer), "list_topics", return_value=mock_metadata):
            producer_cluster_id = _get_cluster_id(producer, "test-topic")

        assert producer_cluster_id == "test-cluster-123"
        assert _cluster_id_by_bootstrap.get(bootstrap) == "test-cluster-123"

        consumer = self._make_consumer(bootstrap)
        # list_topics must NOT be called on the consumer
        consumer.list_topics = MagicMock()  # type: ignore[method-assign]
        consumer_cluster_id = _get_cluster_id(consumer, "test-topic")
        consumer.list_topics.assert_not_called()
        assert consumer_cluster_id == "test-cluster-123"

    def test_consumer_returns_empty_string_when_cache_is_empty(self) -> None:
        """Without a prior producer call the consumer still returns ''."""
        consumer = self._make_consumer("localhost:9092")
        assert _get_cluster_id(consumer, "test-topic") == ""
