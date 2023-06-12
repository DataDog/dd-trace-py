# -*- coding: utf-8 -*-
import kombu

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.kombu import utils
from ddtrace.contrib.kombu.patch import patch
from ddtrace.contrib.kombu.patch import unpatch
from ddtrace.ext import kombu as kombux
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from ..config import RABBITMQ_CONFIG


class TestKombuPatch(TracerTestCase):

    TEST_SERVICE = "kombu-patch"
    TEST_PORT = RABBITMQ_CONFIG["port"]

    def setUp(self):
        super(TestKombuPatch, self).setUp()

        conn = kombu.Connection("amqp://guest:guest@127.0.0.1:{p}//".format(p=self.TEST_PORT))
        conn.connect()
        producer = conn.Producer()
        Pin.override(producer, service=self.TEST_SERVICE, tracer=self.tracer)

        self.conn = conn
        self.producer = producer

        patch()

    def tearDown(self):
        unpatch()

        super(TestKombuPatch, self).tearDown()

    def test_basics(self):
        self._publish_consume()
        self._assert_spans()

    def test_extract_conn_tags(self):
        result = utils.extract_conn_tags(self.conn)
        assert result["out.host"] == "127.0.0.1"
        assert result["network.destination.port"] == str(self.TEST_PORT)

    def _publish_consume(self):
        results = []

        def process_message(body, message):
            results.append(body)
            message.ack()

        task_queue = kombu.Queue("tasks", kombu.Exchange("tasks"), routing_key="tasks")
        to_publish = {"hello": "world"}
        self.producer.publish(
            to_publish, exchange=task_queue.exchange, routing_key=task_queue.routing_key, declare=[task_queue]
        )

        with kombu.Consumer(self.conn, [task_queue], accept=["json"], callbacks=[process_message]) as consumer:
            Pin.override(consumer, service="kombu-patch", tracer=self.tracer)
            self.conn.drain_events(timeout=2)

        self.assertEqual(results[0], to_publish)

    def _assert_spans(self):
        """Tests both producer and consumer tracing"""
        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        producer_span = spans[0]
        assert_is_measured(producer_span)
        self.assertEqual(producer_span.service, self.TEST_SERVICE)
        self.assertEqual(producer_span.name, kombux.PUBLISH_NAME)
        self.assertEqual(producer_span.span_type, "worker")
        self.assertEqual(producer_span.error, 0)
        self.assertEqual(producer_span.get_tag("out.vhost"), "/")
        self.assertEqual(producer_span.get_tag("out.host"), "127.0.0.1")
        self.assertEqual(producer_span.get_tag("kombu.exchange"), u"tasks")
        self.assertEqual(producer_span.get_metric("kombu.body_length"), 18)
        self.assertEqual(producer_span.get_tag("kombu.routing_key"), u"tasks")
        self.assertEqual(producer_span.resource, "tasks")
        self.assertEqual(producer_span.get_tag("component"), "kombu")
        self.assertEqual(producer_span.get_tag("span.kind"), "producer")

        consumer_span = spans[1]
        assert_is_measured(consumer_span)
        self.assertEqual(consumer_span.service, self.TEST_SERVICE)
        self.assertEqual(consumer_span.name, kombux.RECEIVE_NAME)
        self.assertEqual(consumer_span.span_type, "worker")
        self.assertEqual(consumer_span.error, 0)
        self.assertEqual(consumer_span.get_tag("kombu.exchange"), u"tasks")
        self.assertEqual(consumer_span.get_tag("kombu.routing_key"), u"tasks")
        self.assertEqual(consumer_span.get_tag("component"), "kombu")
        self.assertEqual(consumer_span.get_tag("span.kind"), "consumer")

    def test_analytics_default(self):
        self._publish_consume()

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config("kombu", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            self._publish_consume()

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config("kombu", dict(analytics_enabled=True)):
            self._publish_consume()

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)


class TestKombuSettings(TracerTestCase):
    def setUp(self):
        super(TestKombuSettings, self).setUp()

        conn = kombu.Connection("amqp://guest:guest@127.0.0.1:{p}//".format(p=RABBITMQ_CONFIG["port"]))
        conn.connect()
        producer = conn.Producer()
        Pin.override(producer, tracer=self.tracer)

        self.conn = conn
        self.producer = producer

        patch()

    def tearDown(self):
        unpatch()
        super(TestKombuSettings, self).tearDown()


class TestKombuSchematization(TracerTestCase):

    TEST_PORT = RABBITMQ_CONFIG["port"]

    def setUp(self):
        super(TestKombuSchematization, self).setUp()

        conn = kombu.Connection("amqp://guest:guest@127.0.0.1:{p}//".format(p=self.TEST_PORT))
        conn.connect()
        producer = conn.Producer()
        Pin.override(producer, tracer=self.tracer)

        self.conn = conn
        self.producer = producer

        patch()

    def tearDown(self):
        unpatch()

        super(TestKombuSchematization, self).tearDown()

    def _create_schematized_spans(self):
        """
        When a service name is specified by the user
            The kombu integration should use it as the service name for both
            the producer and consumer spans.
        """
        task_queue = kombu.Queue("tasks", kombu.Exchange("tasks"), routing_key="tasks")
        to_publish = {"hello": "world"}

        def process_message(body, message):
            message.ack()

        self.producer.publish(
            to_publish, exchange=task_queue.exchange, routing_key=task_queue.routing_key, declare=[task_queue]
        )

        with kombu.Consumer(self.conn, [task_queue], accept=["json"], callbacks=[process_message]) as consumer:
            Pin.override(consumer, tracer=self.tracer)
            self.conn.drain_events(timeout=2)

        return self.get_spans()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_service_name_default(self):
        spans = self._create_schematized_spans()
        for span in spans:
            assert span.service == "mysvc", "Expected mysvc, got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_service_name_v0(self):
        spans = self._create_schematized_spans()
        for span in spans:
            assert span.service == "mysvc", "Expected mysvc, got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_service_name_v1(self):
        spans = self._create_schematized_spans()
        for span in spans:
            assert span.service == "mysvc", "Expected mysvc, got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_name_default(self):
        spans = self._create_schematized_spans()
        for span in spans:
            assert span.service is None, "Expected None, got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_name_v0(self):
        spans = self._create_schematized_spans()
        for span in spans:
            assert span.service is None, "Expected None, got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_name_v1(self):
        spans = self._create_schematized_spans()
        for span in spans:
            assert (
                span.service == DEFAULT_SPAN_SERVICE_NAME
            ), "Expected internal.schema.DEFAULT_SPAN_SERVICE_NAME got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_operation_name_v0(self):
        spans = self._create_schematized_spans()
        assert spans[0].name == "kombu.publish", "Expected kombu.publish, got {}".format(spans[0].name)
        assert spans[1].name == "kombu.receive", "Expected kombu.receive, got {}".format(spans[1].name)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_operation_name_v1(self):
        spans = self._create_schematized_spans()
        assert spans[0].name == "kombu.send", "Expected kombu.send, got {}".format(spans[0].name)
        assert spans[1].name == "kombu.process", "Expected kombu.process, got {}".format(spans[1].name)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service_producer(self):
        """
        When a service name is specified by the user
            When a parent span with a different service name is provided to the
            producer
                The producer should inherit the parent service name, not the
                global service name.
        """
        task_queue = kombu.Queue("tasks", kombu.Exchange("tasks"), routing_key="tasks")
        to_publish = {"hello": "world"}

        def process_message(body, message):
            message.ack()

        with self.tracer.trace("parent", service="parentsvc"):
            self.producer.publish(
                to_publish, exchange=task_queue.exchange, routing_key=task_queue.routing_key, declare=[task_queue]
            )

        with kombu.Consumer(self.conn, [task_queue], accept=["json"], callbacks=[process_message]) as consumer:
            Pin.override(consumer, tracer=self.tracer)
            self.conn.drain_events(timeout=2)

        spans = self.get_spans()
        self.assertEqual(len(spans), 3)
        # Parent and producer spans should have parent service
        assert spans[0].service == "parentsvc"
        assert spans[1].service == "parentsvc"
        # Consumer span should have global service
        assert spans[2].service == "mysvc"
