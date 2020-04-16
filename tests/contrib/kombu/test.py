# -*- coding: utf-8 -*-
import kombu

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.kombu.patch import patch, unpatch
from ddtrace.contrib.kombu import utils
from ddtrace.ext import kombu as kombux
from ..config import RABBITMQ_CONFIG
from ...base import BaseTracerTestCase
from ...utils import assert_is_measured


class TestKombuPatch(BaseTracerTestCase):

    TEST_SERVICE = 'kombu-patch'
    TEST_PORT = RABBITMQ_CONFIG['port']

    def setUp(self):
        super(TestKombuPatch, self).setUp()

        conn = kombu.Connection('amqp://guest:guest@127.0.0.1:{p}//'.format(p=self.TEST_PORT))
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
        assert result['out.host'] == '127.0.0.1'
        assert result['out.port'] == str(self.TEST_PORT)

    def _publish_consume(self):
        results = []

        def process_message(body, message):
            results.append(body)
            message.ack()

        task_queue = kombu.Queue('tasks', kombu.Exchange('tasks'), routing_key='tasks')
        to_publish = {'hello': 'world'}
        self.producer.publish(to_publish,
                              exchange=task_queue.exchange,
                              routing_key=task_queue.routing_key,
                              declare=[task_queue])

        with kombu.Consumer(self.conn, [task_queue], accept=['json'], callbacks=[process_message]) as consumer:
            Pin.override(consumer, service='kombu-patch', tracer=self.tracer)
            self.conn.drain_events(timeout=2)

        self.assertEqual(results[0], to_publish)

    def _assert_spans(self):
        """Tests both producer and consumer tracing"""
        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        consumer_span = spans[0]
        assert_is_measured(consumer_span)
        self.assertEqual(consumer_span.service, self.TEST_SERVICE)
        self.assertEqual(consumer_span.name, kombux.PUBLISH_NAME)
        self.assertEqual(consumer_span.span_type, 'worker')
        self.assertEqual(consumer_span.error, 0)
        self.assertEqual(consumer_span.get_tag('out.vhost'), '/')
        self.assertEqual(consumer_span.get_tag('out.host'), '127.0.0.1')
        self.assertEqual(consumer_span.get_tag('kombu.exchange'), u'tasks')
        self.assertEqual(consumer_span.get_metric('kombu.body_length'), 18)
        self.assertEqual(consumer_span.get_tag('kombu.routing_key'), u'tasks')
        self.assertEqual(consumer_span.resource, 'tasks')

        producer_span = spans[1]
        assert_is_measured(producer_span)
        self.assertEqual(producer_span.service, self.TEST_SERVICE)
        self.assertEqual(producer_span.name, kombux.RECEIVE_NAME)
        self.assertEqual(producer_span.span_type, 'worker')
        self.assertEqual(producer_span.error, 0)
        self.assertEqual(producer_span.get_tag('kombu.exchange'), u'tasks')
        self.assertEqual(producer_span.get_tag('kombu.routing_key'), u'tasks')

    def test_analytics_default(self):
        self._publish_consume()

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config(
            'kombu',
            dict(analytics_enabled=True, analytics_sample_rate=0.5)
        ):
            self._publish_consume()

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config(
            'kombu',
            dict(analytics_enabled=True)
        ):
            self._publish_consume()

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)


class TestKombuSettings(BaseTracerTestCase):
    def setUp(self):
        super(TestKombuSettings, self).setUp()

        conn = kombu.Connection('amqp://guest:guest@127.0.0.1:{p}//'.format(p=RABBITMQ_CONFIG['port']))
        conn.connect()
        producer = conn.Producer()
        Pin.override(producer, tracer=self.tracer)

        self.conn = conn
        self.producer = producer

        patch()

    def tearDown(self):
        unpatch()
        super(TestKombuSettings, self).tearDown()

    @BaseTracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a service name is specified by the user
            The kombu integration should use it as the service name for both
            the producer and consumer spans.
        """
        task_queue = kombu.Queue('tasks', kombu.Exchange('tasks'), routing_key='tasks')
        to_publish = {'hello': 'world'}

        def process_message(body, message):
            message.ack()

        self.producer.publish(to_publish,
                              exchange=task_queue.exchange,
                              routing_key=task_queue.routing_key,
                              declare=[task_queue])

        with kombu.Consumer(self.conn, [task_queue], accept=['json'], callbacks=[process_message]) as consumer:
            Pin.override(consumer, tracer=self.tracer)
            self.conn.drain_events(timeout=2)

        spans = self.get_spans()
        self.assertEqual(len(spans), 2)

        # Since no parent span exists for the producer it will inherit the
        # global service name.
        for span in spans:
            assert span.service == "mysvc"

    @BaseTracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service_producer(self):
        """
        When a service name is specified by the user
            When a parent span with a different service name is provided to the
            producer
                The producer should inherit the parent service name, not the
                global service name.
        """
        task_queue = kombu.Queue('tasks', kombu.Exchange('tasks'), routing_key='tasks')
        to_publish = {'hello': 'world'}

        def process_message(body, message):
            message.ack()

        with self.tracer.trace("parent", service="parentsvc"):
            self.producer.publish(to_publish,
                                  exchange=task_queue.exchange,
                                  routing_key=task_queue.routing_key,
                                  declare=[task_queue])

        with kombu.Consumer(self.conn, [task_queue], accept=['json'], callbacks=[process_message]) as consumer:
            Pin.override(consumer, tracer=self.tracer)
            self.conn.drain_events(timeout=2)

        spans = self.get_spans()
        self.assertEqual(len(spans), 3)
        # Parent and producer spans should have parent service
        assert spans[0].service == "parentsvc"
        assert spans[1].service == "parentsvc"
        # Consumer span should have global service
        assert spans[2].service == "mysvc"
