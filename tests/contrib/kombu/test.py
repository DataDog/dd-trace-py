# -*- coding: utf-8 -*-
import kombu
from nose.tools import eq_

from ddtrace import Pin
from ddtrace.contrib.kombu.patch import patch, unpatch
from ddtrace.contrib.kombu import utils
from ddtrace.ext import kombu as kombux
from ..config import RABBITMQ_CONFIG
from ...test_tracer import get_dummy_tracer


class TestKombuPatch(object):

    TEST_SERVICE = 'kombu-patch'
    TEST_PORT = RABBITMQ_CONFIG['port']

    def setUp(self):
        conn = kombu.Connection("amqp://guest:guest@127.0.0.1:{p}//".format(p=self.TEST_PORT))
        conn.connect()
        patch()

    def tearDown(self):
        unpatch()

    def test_basics(self):
        conn, producer, tracer = self.get_kombu_and_tracer()
        _assert_conn_traced(conn, producer, tracer, self.TEST_SERVICE)

    def get_kombu_and_tracer(self):
        tracer = get_dummy_tracer()
        conn = kombu.Connection("amqp://guest:guest@127.0.0.1:{p}//".format(p=self.TEST_PORT))
        conn.connect()
        producer = conn.Producer()
        Pin.override(producer, service=self.TEST_SERVICE, tracer=tracer)
        return conn, producer, tracer

    def test_extract_conn_tags(self):
        conn, _, _ = self.get_kombu_and_tracer()
        result = utils.extract_conn_tags(conn)
        assert result['out.host'] == '127.0.0.1'
        assert result['out.port'] == str(self.TEST_PORT)


def _assert_conn_traced(conn, producer, tracer, service):
    """Tests both producer and consumer tracing"""
    results = []

    def process_message(body, message):
        results.append(body)
        message.ack()

    task_queue = kombu.Queue('tasks', kombu.Exchange('tasks'), routing_key='tasks')
    to_publish = {'hello': 'world'}
    producer.publish(to_publish,
                     exchange=task_queue.exchange,
                     routing_key=task_queue.routing_key,
                     declare=[task_queue])

    with kombu.Consumer(conn, [task_queue], accept=['json'], callbacks=[process_message]) as consumer:
        Pin.override(consumer, service='kombu-patch', tracer=tracer)
        conn.drain_events(timeout=2)

    eq_(results[0], to_publish)
    spans = tracer.writer.pop()
    eq_(len(spans), 2)
    consumer_span = spans[0]
    eq_(consumer_span.service, service)
    eq_(consumer_span.name, kombux.PUBLISH_NAME)
    eq_(consumer_span.span_type, 'kombu')
    eq_(consumer_span.error, 0)
    eq_(consumer_span.get_tag('out.vhost'), '/')
    eq_(consumer_span.get_tag('out.host'), '127.0.0.1')
    eq_(consumer_span.get_tag('kombu.exchange'), u'tasks')
    eq_(consumer_span.get_metric('kombu.body_length'), 18)
    eq_(consumer_span.get_tag('kombu.routing_key'), u'tasks')
    eq_(consumer_span.resource, 'tasks')

    producer_span = spans[1]
    eq_(producer_span.service, service)
    eq_(producer_span.name, kombux.RECEIVE_NAME)
    eq_(producer_span.span_type, 'kombu')
    eq_(producer_span.error, 0)
    eq_(producer_span.get_tag('kombu.exchange'), u'tasks')
    eq_(producer_span.get_tag('kombu.routing_key'), u'tasks')
