# -*- coding: utf-8 -*-

import confluent_kafka

from ddtrace import Pin
from ddtrace.contrib.kafka.patch import patch
from ddtrace.contrib.kafka.patch import unpatch
from tests.utils import TracerTestCase

from ..config import KAFKA_CONFIG


class TestKafkaPatch(TracerTestCase):
    TEST_PORT = KAFKA_CONFIG["port"]

    def setUp(self):
        self.topic_name = "test_topic"
        self.group_id = "test_group"
        self.bootstrap_servers = "localhost:{}".format(self.TEST_PORT)

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
        self.consumer.close()
        unpatch()
        super(TestKafkaPatch, self).tearDown()

    def test_produce(self):
        payload = bytes("hueh hueh hueh", encoding="utf-8")

        self.producer.produce(self.topic_name, payload)
        self.producer.flush()

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        self.assert_is_measured(span)
        assert span.service == "iamkafka"
        assert span.name == "kafkafoo"
        assert span.span_type == "kafkabar"
        assert span.error == 0
        meta = {
            "topic": "banana_topic",
            "bootstrap_servers": "numnah",
        }
        for k, v in meta.items():
            assert span.get_tag(k) == v

    def __test_consume(self):
        payload = bytes("hueh hueh hueh", encoding="utf-8")

        self.producer.produce(self.topic_name, payload)
        self.producer.flush()
        message = None
        while message is None:
            message = self.consumer.poll(1.0)
        assert message.value() == payload

        spans = self.get_spans()
        assert len(spans) > 1

        for span in spans[1:]:
            self.assert_is_measured(span)
            assert span.service == "iamkafka"
            assert span.name == "kafkafoo"
            assert span.span_type == "kafkabar"
            assert span.error == 0
            meta = {
                "out.bootstrap_servers": self.bootstrap_servers,
                "out.topic": self.topic_name,
                "out.group_id": self.group_id,
            }
            for k, v in meta.items():
                assert span.get_tag(k) == v
