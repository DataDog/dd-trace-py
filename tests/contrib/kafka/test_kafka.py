# -*- coding: utf-8 -*-

import confluent_kafka

from ddtrace.contrib.kafka.patch import patch
from ddtrace.contrib.kafka.patch import unpatch
from tests.utils import TracerTestCase

from ..config import KAFKA_CONFIG


class TestKafkaPatch(TracerTestCase):
    TEST_PORT = KAFKA_CONFIG["port"]

    def setUp(self):
        super(TestKafkaPatch, self).setUp()
        patch()
        self.producer = confluent_kafka.Producer({"bootstrap.servers": "localhost:{}".format(self.TEST_PORT)})
        self.consumer = confluent_kafka.Consumer(
            {
                "bootstrap.servers": "localhost:{}".format(self.TEST_PORT),
                "group.id": "test_group",
                "auto.offset.reset": "earliest",
            }
        )
        self.consumer.subscribe(["test_topic"])

    def tearDown(self):
        self.consumer.close()
        unpatch()
        super(TestKafkaPatch, self).tearDown()

    def test_produce(self):
        self.producer.produce("test_topic", bytes("hueh hueh hueh", encoding="utf-8"))
        self.producer.flush()
        message = None
        while message is None:
            message = self.consumer.poll(1.0)
        print(message)
        assert message is not None

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        self.assert_is_measured(span)
        assert span.service == "redis"
        assert span.name == "redis.command"
        assert span.span_type == "redis"
        assert span.error == 0
        meta = {
            "out.host": u"localhost",
        }
        metrics = {
            "out.port": self.TEST_PORT,
            "out.redis_db": 0,
        }
        for k, v in meta.items():
            assert span.get_tag(k) == v
        for k, v in metrics.items():
            assert span.get_metric(k) == v

        assert span.get_tag("redis.raw_command").startswith(u"MGET 0 1 2 3")
        assert span.get_tag("redis.raw_command").endswith(u"...")
        assert span.get_tag("component") == "redis"
