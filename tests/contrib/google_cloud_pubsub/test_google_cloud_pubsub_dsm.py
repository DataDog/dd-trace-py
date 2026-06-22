import os
import threading

from google.cloud import pubsub_v1

from ddtrace.contrib.internal.google_cloud_pubsub.patch import patch
from ddtrace.contrib.internal.google_cloud_pubsub.patch import unpatch
from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.datastreams.processor import DataStreamsCtx
from ddtrace.internal.native import DDSketch
from tests.contrib.google_cloud_pubsub.conftest import EMULATOR_HOST
from tests.contrib.google_cloud_pubsub.conftest import PROJECT_ID
from tests.contrib.google_cloud_pubsub.conftest import SUBSCRIPTION_ID
from tests.contrib.google_cloud_pubsub.conftest import TOPIC_ID
from tests.datastreams.utils import all_pathway_stat_keys
from tests.utils import TracerTestCase


DSM_TEST_PATH_HEADER_SIZE = 28


def _wait_for_pathway_directions(processor, *required, timeout=10.0):
    """Poll DSM buckets until checkpoints for each required direction tag appear."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        tag_strs = [key[0] for key in all_pathway_stat_keys(processor)]
        if all(any(req in tags for tags in tag_strs) for req in required):
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for DSM checkpoints {required}; saw: {tag_strs}")


class PubSubDSMTest(TracerTestCase):
    def setUp(self):
        os.environ["PUBSUB_EMULATOR_HOST"] = EMULATOR_HOST
        patch()

        self.publisher = pubsub_v1.PublisherClient()
        topic = self.publisher.create_topic(name=f"projects/{PROJECT_ID}/topics/{TOPIC_ID}")
        self.topic_path = topic.name

        self.subscriber = pubsub_v1.SubscriberClient()
        sub_path = self.subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_ID)
        self.subscriber.create_subscription(name=sub_path, topic=self.topic_path)
        self.subscription_path = sub_path

        self.processor = data_streams_processor(reset=True)

        super().setUp()

    def tearDown(self):
        try:
            self.subscriber.delete_subscription(subscription=self.subscription_path)
            self.publisher.delete_topic(topic=self.topic_path)
        finally:
            self.subscriber.close()
            self.publisher.transport.close()
            self.processor.shutdown(timeout=5)
            unpatch()
        super().tearDown()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_dsm_payload_size_produce(self):
        """Producer pathway records a payload size that accounts for data, attributes,
        and the injected pathway header.
        """
        payload = b"data streams hello"
        test_attrs = {"custom_key": "custom_value"}
        self.publisher.publish(self.topic_path, payload, **test_attrs).result(timeout=10)

        _wait_for_pathway_directions(self.processor, "direction:out")

        found_produce_sketch = False
        with self.processor._lock:
            for bucket in self.processor._buckets.values():
                for key, stats in bucket.pathway_stats.items():
                    tags = key[0]
                    if "direction:out" in tags and "topic:" in tags:
                        assert "type:google-pubsub" in tags
                        assert stats.payload_size.count >= 1
                        found_produce_sketch = True
        assert found_produce_sketch, "expected producer pathway sketch not recorded"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_dsm_pathway_linkage(self):
        """Publishing then subscribing produces linked producer->consumer pathway hashes
        with the expected tag schema.
        """
        received = threading.Event()

        def callback(message):
            message.ack()
            received.set()

        future = self.subscriber.subscribe(self.subscription_path, callback=callback)
        try:
            self.publisher.publish(self.topic_path, b"data streams hello").result(timeout=10)
            assert received.wait(timeout=10), "timed out waiting for subscriber callback"
        finally:
            future.cancel()
            future.result(timeout=5)

        _wait_for_pathway_directions(self.processor, "direction:out", "direction:in")

        ctx = DataStreamsCtx(self.processor, 0, 0, 0)
        parent_hash = ctx._compute_hash(
            sorted(["direction:out", f"topic:{self.topic_path}", "type:google-pubsub"]),
            0,
        )
        child_hash = ctx._compute_hash(
            sorted(["direction:in", f"topic:{self.subscription_path}", "type:google-pubsub"]),
            parent_hash,
        )
        hash_pairs = {(key[1], key[2]) for key in all_pathway_stat_keys(self.processor)}
        assert (parent_hash, 0) in hash_pairs, f"producer hash missing; saw {hash_pairs}"
        assert (child_hash, parent_hash) in hash_pairs, f"consumer hash missing; saw {hash_pairs}"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_dsm_pathway_header_injected_on_publish(self):
        """The dd-pathway-ctx-base64 attribute is injected into published messages and survives the round trip."""
        self.publisher.publish(self.topic_path, b"data streams hello", custom_key="custom_value").result(timeout=10)

        response = self.subscriber.pull(subscription=self.subscription_path, max_messages=1, timeout=10)
        assert len(response.received_messages) == 1
        attributes = dict(response.received_messages[0].message.attributes)
        assert PROPAGATION_KEY_BASE_64 in attributes
        assert attributes[PROPAGATION_KEY_BASE_64]
        # User-provided attributes are preserved alongside the injected pathway key.
        assert attributes["custom_key"] == "custom_value"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_dsm_payload_size_matches_expected(self):
        """With distributed tracing disabled, payload size = data + attrs + injected pathway key + path header bytes."""
        from tests.utils import override_config

        payload = b"abcdef"  # 6 bytes
        test_attrs = {"k1": "v1"}  # 2 + 2 = 4 bytes of attribute content
        with override_config("google_cloud_pubsub", dict(distributed_tracing_enabled=False)):
            self.publisher.publish(self.topic_path, payload, **test_attrs).result(timeout=10)

        _wait_for_pathway_directions(self.processor, "direction:out")

        expected_payload_size = float(len(payload) + 4 + len(PROPAGATION_KEY_BASE_64) + DSM_TEST_PATH_HEADER_SIZE)
        expected_sketch = DDSketch()
        expected_sketch.add(expected_payload_size)
        expected_proto = expected_sketch.to_proto()

        with self.processor._lock:
            produce_stats = [
                stats
                for bucket in self.processor._buckets.values()
                for key, stats in bucket.pathway_stats.items()
                if "direction:out" in key[0]
            ]
        assert len(produce_stats) >= 1
        for stats in produce_stats:
            assert stats.payload_size.count >= 1
            assert stats.payload_size.to_proto() == expected_proto
