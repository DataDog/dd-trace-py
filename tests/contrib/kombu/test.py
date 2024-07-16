# -*- coding: utf-8 -*-
import kombu
import mock

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.kombu import utils
from ddtrace.contrib.kombu.patch import patch
from ddtrace.contrib.kombu.patch import unpatch
from ddtrace.ext import kombu as kombux
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from ..config import RABBITMQ_CONFIG


DSM_TEST_PATH_HEADER_SIZE = 28


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
        self.assertEqual(producer_span.get_tag("kombu.exchange"), "tasks")
        self.assertEqual(producer_span.get_metric("kombu.body_length"), 18)
        self.assertEqual(producer_span.get_tag("kombu.routing_key"), "tasks")
        self.assertEqual(producer_span.resource, "tasks")
        self.assertEqual(producer_span.get_tag("component"), "kombu")
        self.assertEqual(producer_span.get_tag("span.kind"), "producer")

        consumer_span = spans[1]
        assert_is_measured(consumer_span)
        self.assertEqual(consumer_span.service, self.TEST_SERVICE)
        self.assertEqual(consumer_span.name, kombux.RECEIVE_NAME)
        self.assertEqual(consumer_span.span_type, "worker")
        self.assertEqual(consumer_span.error, 0)
        self.assertEqual(consumer_span.get_tag("kombu.exchange"), "tasks")
        self.assertEqual(consumer_span.get_tag("kombu.routing_key"), "tasks")
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

    def _gen_distributed_spans(self):
        self._publish_consume()
        spans = self.get_spans()
        rcv = [s for s in spans if s.name == "kombu.receive"][0]
        send = [s for s in spans if s.name == "kombu.publish"][0]
        return rcv, send

    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_distributed_tracing_basic_default(self):
        rcv, send = self._gen_distributed_spans()
        assert rcv.trace_id == send.trace_id, f"rcv.trace_id {rcv.trace_id}!= send.trace_id {send.trace_id}"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_KOMBU_DISTRIBUTED_TRACING="True"))
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_distributed_tracing_basic_force_on(self):
        rcv, send = self._gen_distributed_spans()
        assert rcv.trace_id == send.trace_id, f"rcv.trace_id {rcv.trace_id}!= send.trace_id {send.trace_id}"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_KOMBU_DISTRIBUTED_TRACING="False"))
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_distributed_tracing_basic_force_off(self):
        rcv, send = self._gen_distributed_spans()
        assert rcv.trace_id != send.trace_id, f"rcv.trace_id {rcv.trace_id} == send.trace_id {send.trace_id}"


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


class TestKombuDsm(TracerTestCase):
    def setUp(self):
        super(TestKombuDsm, self).setUp()

        self.conn = kombu.Connection("amqp://guest:guest@127.0.0.1:{p}//".format(p=RABBITMQ_CONFIG["port"]))
        self.conn.connect()
        self.producer = self.conn.Producer()
        Pin.override(self.producer, tracer=self.tracer)

        self.patcher = mock.patch(
            "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
        )
        self.patcher.start()
        from ddtrace.internal.datastreams import data_streams_processor

        self.processor = data_streams_processor()
        self.processor.stop()

        patch()

    def tearDown(self):
        self.patcher.stop()
        unpatch()
        super(TestKombuDsm, self).tearDown()

    def _publish_consume(self, message={"hello": "world"}, exchange="dsm_tests"):
        results = []
        queue_name = None

        def process_message(body, message):
            results.append(body)
            message.ack()

        to_publish = message
        task_queue = None
        if exchange:
            task_queue = kombu.Queue("tasks", kombu.Exchange(exchange), routing_key=exchange)
            self.producer.publish(
                to_publish, exchange=task_queue.exchange, routing_key=task_queue.routing_key, declare=[task_queue]
            )
        else:
            task_queue = kombu.Queue("tasks", kombu.Exchange(), routing_key="tasks")
            self.producer.publish(to_publish, routing_key=task_queue.routing_key, declare=[task_queue])

        with kombu.Consumer(self.conn, [task_queue], accept=["json"], callbacks=[process_message]) as consumer:
            Pin.override(consumer, service="kombu-patch", tracer=self.tracer)
            self.conn.drain_events(timeout=2)
            queue_name = consumer.channel.queue_declare("tasks", passive=True).queue

        self.assertEqual(results[0], to_publish)
        return queue_name

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_data_streams_basic(self):
        queue_name = self._publish_consume()
        buckets = self.processor._buckets
        assert len(buckets) == 1
        first = list(buckets.values())[0].pathway_stats

        out_tags = ",".join(["direction:out", "exchange:dsm_tests", "has_routing_key:true", "type:rabbitmq"])
        in_tags = ",".join(["direction:in", f"topic:{queue_name}", "type:rabbitmq"])

        assert first[(out_tags, 72906486983046225, 0)].full_pathway_latency.count == 1
        assert first[(out_tags, 72906486983046225, 0)].edge_latency.count == 1
        assert first[(in_tags, 14415630735402874533, 72906486983046225)].full_pathway_latency.count == 1
        assert first[(in_tags, 14415630735402874533, 72906486983046225)].edge_latency.count == 1

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_DATA_STREAMS_ENABLED="True", DD_KOMBU_DISTRIBUTED_TRACING="False")
    )
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_data_streams_payload(self):
        for payload, payload_size in [({"key": "test"}, 15), ({"key2": "你"}, 18)]:
            expected_payload_size = payload_size
            expected_payload_size += len(PROPAGATION_KEY_BASE_64)  # Add in header key length
            expected_payload_size += DSM_TEST_PATH_HEADER_SIZE  # to account for path header we add
            self.processor._buckets.clear()
            _queue_name = self._publish_consume(message=payload)
            buckets = self.processor._buckets
            assert len(buckets) == 1

            first = list(buckets.values())[0].pathway_stats
            for _bucket_name, bucket in first.items():
                print(payload)
                print(payload_size)
                assert bucket.payload_size.count >= 1
                assert (
                    bucket.payload_size.sum == expected_payload_size
                ), f"Actual payload size: {bucket.payload_size.sum} != Expected payload size: {expected_payload_size}"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_data_streams_direct_exchange(self):
        queue_name = self._publish_consume(exchange=None)
        buckets = self.processor._buckets
        assert len(buckets) == 1
        first = list(buckets.values())[0].pathway_stats

        out_tags = ",".join(["direction:out", "exchange:", "has_routing_key:true", "type:rabbitmq"])
        in_tags = ",".join(["direction:in", f"topic:{queue_name}", "type:rabbitmq"])

        assert first[(out_tags, 2585352008533360777, 0)].full_pathway_latency.count == 1
        assert first[(out_tags, 2585352008533360777, 0)].edge_latency.count == 1
        assert first[(in_tags, 10011432234075651806, 2585352008533360777)].full_pathway_latency.count == 1
        assert first[(in_tags, 10011432234075651806, 2585352008533360777)].edge_latency.count == 1
