import confluent_kafka
import six

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.kafka.patch import patch
from ddtrace.contrib.kafka.patch import unpatch
from tests.utils import TracerTestCase
from tests.utils import override_config

from ..config import KAFKA_CONFIG


class TestKafkaPatch(TracerTestCase):
    TEST_PORT = KAFKA_CONFIG["port"]

    def setUp(self):
        self.topic_name = "test_topic"
        self.group_id = "test_group"
        self.bootstrap_servers = "localhost:{}".format(self.TEST_PORT)
        if six.PY3:
            self.payload = bytes("hueh hueh hueh", encoding="utf-8")
        else:
            self.payload = bytes("hueh hueh hueh")

        super(TestKafkaPatch, self).setUp()
        patch()

        self.producer = confluent_kafka.Producer({"bootstrap.servers": self.bootstrap_servers})
        self.consumer = confluent_kafka.Consumer(
            {
                "bootstrap.servers": self.bootstrap_servers,
                "group.id": self.group_id,
                "auto.offset.reset": "earliest",
            }
        )
        Pin.override(self.producer, tracer=self.tracer)
        Pin.override(self.consumer, tracer=self.tracer)
        self.consumer.subscribe(["test_topic"])

    def tearDown(self):
        self.producer.purge()
        self.consumer.close()
        unpatch()
        super(TestKafkaPatch, self).tearDown()

    def test_produce(self):
        self.producer.produce(self.topic_name, self.payload, key="test_key")
        self.producer.flush()
        message = None
        while message is None:
            message = self.consumer.poll(1.0)

        spans = self.get_spans()
        assert len(spans) >= 2
        span = spans[0]

        self.assert_is_measured(span)
        assert span.service == "kafka"
        assert span.name == "kafka.produce"
        assert span.span_type == "worker"
        assert span.error == 0
        assert span.get_metric("kafka.partition") == -1
        meta = {
            "messaging.system": "kafka",
            "span.kind": "producer",
            "kafka.topic": "test_topic",
            "kafka.message_key": "test_key",
            "kafka.tombstone": "False",
        }
        for k, v in meta.items():
            assert span.get_tag(k) == v

    def test_produce_tombstone(self):
        self.producer.produce(self.topic_name, key="test_key")
        self.producer.flush()
        message = None
        while message is None:
            message = self.consumer.poll(1.0)

        spans = self.get_spans()
        assert len(spans) >= 1
        span = spans[0]

        self.assert_is_measured(span)
        assert span.service == "kafka"
        assert span.name == "kafka.produce"
        assert span.span_type == "worker"
        assert span.error == 0
        assert span.get_metric("kafka.partition") == -1
        meta = {
            "messaging.system": "kafka",
            "span.kind": "producer",
            "kafka.topic": "test_topic",
            "kafka.message_key": "test_key",
            "kafka.tombstone": "True",
        }
        for k, v in meta.items():
            assert span.get_tag(k) == v

    def test_consume(self):
        self.producer.produce(self.topic_name, self.payload, key="test_key")
        self.producer.flush()
        message = None
        while message is None:
            message = self.consumer.poll(1.0)
        assert message.value() == self.payload

        spans = self.get_spans()
        assert len(spans) > 1

        assert spans[0].name == "kafka.produce"
        poll_span_received_message = False
        for span in spans[1:]:
            self.assert_is_measured(span)
            assert span.service == "kafka"
            assert span.name == "kafka.consume"
            assert span.span_type == "worker"
            assert span.error == 0
            if span.get_tag("kafka.received_message") == "True":
                assert span.get_metric("kafka.partition") == message.partition()
                meta = {
                    "messaging.system": "kafka",
                    "span.kind": "consumer",
                    "kafka.topic": "test_topic",
                    "kafka.message_key": "test_key",
                    "kafka.tombstone": "False",
                }
                poll_span_received_message = True
            else:
                meta = {
                    "messaging.system": "kafka",
                    "span.kind": "consumer",
                }
            for k, v in meta.items():
                assert span.get_tag(k) == v
        assert poll_span_received_message

    def test_consume_tombstone(self):
        self.producer.produce(self.topic_name)
        self.producer.flush()
        message = None
        while message is None:
            message = self.consumer.poll(1.0)
        assert message.value() is None

        spans = self.get_spans()
        assert len(spans) > 1

        assert spans[0].name == "kafka.produce"
        for span in spans[1:]:
            self.assert_is_measured(span)
            assert span.service == "kafka"
            assert span.name == "kafka.consume"
            assert span.span_type == "worker"
            assert span.error == 0
            if span.get_tag("kafka.received_message") == "True":
                assert span.get_metric("kafka.partition") == message.partition()
                meta = {
                    "messaging.system": "kafka",
                    "span.kind": "consumer",
                    "kafka.topic": "test_topic",
                    "kafka.message_key": "",
                    "kafka.tombstone": "True",
                }
                for k, v in meta.items():
                    assert span.get_tag(k) == v

    def test_service_override(self):
        with override_config("kafka", dict(service="my-custom-service-name")):
            self.producer.produce(self.topic_name, self.payload)
            self.producer.flush()
            message = None
            while message is None:
                message = self.consumer.poll(1.0)
            assert message.value() == self.payload

        spans = self.get_spans()
        for span in spans:
            self.assert_is_measured(span)
            assert span.service == "my-custom-service-name"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_KAFKA_SERVICE="my-custom-service-name"))
    def test_user_specified_service(self):
        self.producer.produce(self.topic_name, self.payload)
        self.producer.flush()
        message = None
        while message is None:
            message = self.consumer.poll(1.0)
        assert message.value() == self.payload

        spans = self.get_spans()
        for span in spans:
            self.assert_is_measured(span)
            assert span.service == "my-custom-service-name"

    def test_analytics_default(self):
        self.producer.produce(self.topic_name, self.payload, key="test_key")
        self.producer.flush()
        message = None
        while message is None:
            message = self.consumer.poll(1.0)

        spans = self.get_spans()
        assert len(spans) >= 2
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config("kafka", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            self.producer.produce(self.topic_name, self.payload, key="test_key")
            self.producer.flush()
            message = None
            while message is None:
                message = self.consumer.poll(1.0)

        spans = self.get_spans()
        assert len(spans) >= 2
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config("kafka", dict(analytics_enabled=True)):
            self.producer.produce(self.topic_name, self.payload, key="test_key")
            self.producer.flush()
            message = None
            while message is None:
                message = self.consumer.poll(1.0)

        spans = self.get_spans()
        assert len(spans) >= 2
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)
