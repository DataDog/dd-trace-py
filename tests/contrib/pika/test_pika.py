from unittest import mock
import uuid

import pika
import pytest

from ddtrace.contrib.internal.pika.patch import patch
from ddtrace.contrib.internal.pika.patch import unpatch
from ddtrace.ext import pika as pikax
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from ..config import RABBITMQ_CONFIG


def _parameters():
    return pika.ConnectionParameters(
        host=RABBITMQ_CONFIG["host"],
        port=RABBITMQ_CONFIG["port"],
        credentials=pika.PlainCredentials(RABBITMQ_CONFIG["user"], RABBITMQ_CONFIG["password"]),
    )


class PikaTestCase(TracerTestCase):
    def setUp(self):
        super().setUp()
        patch()
        self.connection = pika.BlockingConnection(_parameters())
        self.channel = self.connection.channel()
        self.suffix = uuid.uuid4().hex[:8]
        self.exchange = f"dd-pika-exchange-{self.suffix}"
        self.routing_key = f"dd-pika-rk-{self.suffix}"
        self.queue = f"dd-pika-queue-{self.suffix}"
        self.channel.exchange_declare(exchange=self.exchange, exchange_type="direct", auto_delete=True)
        self.channel.queue_declare(queue=self.queue, exclusive=True)
        self.channel.queue_bind(queue=self.queue, exchange=self.exchange, routing_key=self.routing_key)

    def tearDown(self):
        if self.connection.is_open:
            self.connection.close()
        unpatch()
        super().tearDown()

    def _publish(self, body=b"hello", exchange=None, routing_key=None, properties=None):
        self.channel.basic_publish(
            exchange=self.exchange if exchange is None else exchange,
            routing_key=self.routing_key if routing_key is None else routing_key,
            body=body,
            properties=properties,
        )

    def _consume_one(self, queue=None):
        received = []

        def on_message(ch, method, properties, body):
            received.append((method, properties, body, self.tracer.current_span()))
            ch.basic_ack(delivery_tag=method.delivery_tag)
            ch.stop_consuming()

        self.channel.basic_consume(queue=queue or self.queue, on_message_callback=on_message)
        self.connection.call_later(5, self.channel.stop_consuming)
        self.channel.start_consuming()
        assert len(received) == 1
        return received[0]

    def _spans_by_name(self):
        spans = self.get_spans()
        return {name: [s for s in spans if s.name == name] for name in (pikax.PUBLISH_NAME, pikax.RECEIVE_NAME)}


class TestPikaTracing(PikaTestCase):
    def test_publish_and_basic_consume(self):
        self._publish()
        _method, properties, body, active_span = self._consume_one()

        assert body == b"hello"
        spans = self._spans_by_name()
        (publish,) = spans[pikax.PUBLISH_NAME]
        (receive,) = spans[pikax.RECEIVE_NAME]

        assert_is_measured(publish)
        assert publish.span_type == "worker"
        assert publish.resource == self.exchange
        assert publish.get_tag("span.kind") == "producer"
        assert publish.get_tag("component") == "pika"
        assert publish.get_tag(pikax.EXCHANGE) == self.exchange
        assert publish.get_tag(pikax.ROUTING_KEY) == self.routing_key
        assert publish.get_tag("out.host") == RABBITMQ_CONFIG["host"]
        assert publish.get_tag("out.vhost") == "/"
        assert publish.get_metric(pikax.BODY_LEN) == 5

        assert receive.resource == self.queue
        assert receive.get_tag("span.kind") == "consumer"
        assert receive.get_tag(pikax.QUEUE) == self.queue
        assert receive.get_tag(pikax.METHOD) == "basic_consume"
        assert receive.trace_id == publish.trace_id
        assert receive.parent_id == publish.span_id
        assert active_span is receive
        assert "x-datadog-trace-id" in properties.headers

    def test_caller_properties_not_mutated(self):
        headers = {"app": "value"}
        properties = pika.BasicProperties(content_type="text/plain", headers=headers)
        self._publish(properties=properties)
        _method, received_properties, _body, _span = self._consume_one()

        assert headers == {"app": "value"}
        assert properties.headers is headers
        assert received_properties.content_type == "text/plain"
        assert received_properties.headers["app"] == "value"
        assert "x-datadog-trace-id" in received_properties.headers

    def test_basic_get(self):
        self._publish()
        method, _properties, body = None, None, None
        for _ in range(50):
            method, _properties, body = self.channel.basic_get(queue=self.queue, auto_ack=True)
            if method is not None:
                break
            self.connection.sleep(0.1)

        assert body == b"hello"
        (publish,) = self._spans_by_name()[pikax.PUBLISH_NAME]
        (receive,) = self._spans_by_name()[pikax.RECEIVE_NAME]
        assert receive.get_tag(pikax.METHOD) == "basic_get"
        assert receive.get_tag(pikax.QUEUE) == self.queue
        assert receive.parent_id == publish.span_id

    def test_consume_generator(self):
        self._publish()
        received = None
        stream = self.channel.consume(self.queue, inactivity_timeout=5)
        for method, _properties, body in stream:
            received = body
            self.channel.basic_ack(method.delivery_tag)
            break
        stream.close()
        self.channel.cancel()

        assert received == b"hello"
        (publish,) = self._spans_by_name()[pikax.PUBLISH_NAME]
        (receive,) = self._spans_by_name()[pikax.RECEIVE_NAME]
        assert receive.get_tag(pikax.METHOD) == "consume"
        assert receive.parent_id == publish.span_id
        assert receive.error == 0

    def test_consume_generator_processing_nests_under_receive_span(self):
        forward_queue = f"dd-pika-fwd-{self.suffix}"
        self.channel.queue_declare(queue=forward_queue, exclusive=True)
        self._publish()

        stream = self.channel.consume(self.queue, inactivity_timeout=5)
        for method, _properties, _body in stream:
            active_span = self.tracer.current_span()
            self.channel.basic_ack(method.delivery_tag)
            self.channel.basic_publish(exchange="", routing_key=forward_queue, body=b"forwarded")
            break
        stream.close()
        self.channel.cancel()

        spans = self._spans_by_name()
        (receive,) = spans[pikax.RECEIVE_NAME]
        (forward,) = [s for s in spans[pikax.PUBLISH_NAME] if s.resource == forward_queue]
        assert active_span is receive
        assert forward.parent_id == receive.span_id
        assert receive.error == 0
        assert receive.get_tag("error.type") is None

    def test_consume_generator_inactivity_timeout(self):
        items = []
        stream = self.channel.consume(self.queue, inactivity_timeout=0.2)
        for item in stream:
            items.append(item)
            break
        stream.close()
        self.channel.cancel()

        assert items == [(None, None, None)]
        assert self._spans_by_name()[pikax.RECEIVE_NAME] == []

    def test_one_receive_span_per_message(self):
        for i in range(3):
            self._publish(body=f"m{i}".encode())

        seen = []

        def on_message(ch, method, properties, body):
            seen.append(body)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            if len(seen) == 3:
                ch.stop_consuming()

        self.channel.basic_consume(queue=self.queue, on_message_callback=on_message)
        self.connection.call_later(5, self.channel.stop_consuming)
        self.channel.start_consuming()

        spans = self._spans_by_name()
        assert seen == [b"m0", b"m1", b"m2"]
        assert len(spans[pikax.PUBLISH_NAME]) == 3
        assert len(spans[pikax.RECEIVE_NAME]) == 3
        parents = {s.span_id for s in spans[pikax.PUBLISH_NAME]}
        assert {s.parent_id for s in spans[pikax.RECEIVE_NAME]} == parents

    def test_callback_error_is_recorded(self):
        self._publish()

        def on_message(ch, method, properties, body):
            raise ValueError("boom")

        self.channel.basic_consume(queue=self.queue, on_message_callback=on_message, auto_ack=True)
        with pytest.raises(ValueError):
            self.connection.process_data_events(time_limit=5)

        (receive,) = self._spans_by_name()[pikax.RECEIVE_NAME]
        assert receive.error == 1
        assert receive.get_tag("error.type") == "builtins.ValueError"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_PIKA_DISTRIBUTED_TRACING="false"))
    def test_distributed_tracing_disabled(self):
        self._publish()
        _method, properties, _body, _span = self._consume_one()

        assert "x-datadog-trace-id" not in (properties.headers or {})
        (publish,) = self._spans_by_name()[pikax.PUBLISH_NAME]
        (receive,) = self._spans_by_name()[pikax.RECEIVE_NAME]
        assert receive.trace_id != publish.trace_id

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_service_defaults_to_global_service(self):
        self._publish()
        self._consume_one()
        spans = self._spans_by_name()
        assert spans[pikax.PUBLISH_NAME][0].service == "mysvc"
        assert spans[pikax.RECEIVE_NAME][0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_PIKA_SERVICE="custom-rabbit"))
    def test_service_override(self):
        self._publish()
        self._consume_one()
        spans = self._spans_by_name()
        assert spans[pikax.PUBLISH_NAME][0].service == "custom-rabbit"
        assert spans[pikax.RECEIVE_NAME][0].service == "custom-rabbit"


class TestPikaSelectConnection(TracerTestCase):
    def setUp(self):
        super().setUp()
        patch()

    def tearDown(self):
        unpatch()
        super().tearDown()

    def test_select_connection_publish_and_consume(self):
        queue = f"dd-pika-select-{uuid.uuid4().hex[:8]}"
        received = []
        active = []

        def on_message(channel, method, properties, body):
            received.append(body)
            active.append(self.tracer.current_span())
            channel.basic_ack(method.delivery_tag)
            channel.connection.close()

        def on_queue_declared(channel):
            channel.basic_consume(queue=queue, on_message_callback=on_message)
            channel.basic_publish(exchange="", routing_key=queue, body=b"select")

        def on_channel_open(channel):
            channel.queue_declare(queue=queue, exclusive=True, callback=lambda _frame: on_queue_declared(channel))

        def on_open(connection):
            connection.channel(on_open_callback=on_channel_open)

        connection = pika.SelectConnection(
            _parameters(),
            on_open_callback=on_open,
            on_close_callback=lambda conn, _reason: conn.ioloop.stop(),
        )
        connection.ioloop.call_later(5, connection.ioloop.stop)
        connection.ioloop.start()

        assert received == [b"select"]
        spans = self.get_spans()
        (publish,) = [s for s in spans if s.name == pikax.PUBLISH_NAME]
        (receive,) = [s for s in spans if s.name == pikax.RECEIVE_NAME]
        assert publish.resource == queue
        assert receive.parent_id == publish.span_id
        assert active[0] is receive


class TestPikaDsm(PikaTestCase):
    def setUp(self):
        super().setUp()
        from ddtrace.internal.datastreams import data_streams_processor

        self.processor = data_streams_processor()

    def _pathway_stats(self):
        buckets = self.processor._buckets
        assert len(buckets) == 1
        return list(buckets.values())[0].pathway_stats

    def _entry(self, stats, tags):
        matches = [(h, ph, s) for (t, h, ph), s in stats.items() if t == ",".join(tags)]
        assert len(matches) == 1, list(stats.keys())
        return matches[0]

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_named_exchange(self):
        self._publish()
        _method, properties, _body, _span = self._consume_one()
        assert PROPAGATION_KEY_BASE_64 in properties.headers

        stats = self._pathway_stats()
        out_hash, out_parent, _ = self._entry(
            stats, ["direction:out", f"exchange:{self.exchange}", "has_routing_key:true", "type:rabbitmq"]
        )
        _in_hash, in_parent, _ = self._entry(stats, ["direction:in", f"topic:{self.queue}", "type:rabbitmq"])
        assert out_parent == 0
        assert in_parent == out_hash

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_default_exchange_forwarding_chain(self):
        forward_queue = f"dd-pika-forward-{self.suffix}"
        self.channel.queue_declare(queue=forward_queue, exclusive=True)
        self._publish()

        def forward(ch, method, properties, body):
            ch.basic_publish(exchange="", routing_key=forward_queue, body=b"forwarded " + body)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            ch.stop_consuming()

        self.channel.basic_consume(queue=self.queue, on_message_callback=forward)
        self.connection.call_later(5, self.channel.stop_consuming)
        self.channel.start_consuming()
        self._consume_one(queue=forward_queue)

        stats = self._pathway_stats()
        out1, _, _ = self._entry(
            stats, ["direction:out", f"exchange:{self.exchange}", "has_routing_key:true", "type:rabbitmq"]
        )
        in1, in1_parent, _ = self._entry(stats, ["direction:in", f"topic:{self.queue}", "type:rabbitmq"])
        out2, out2_parent, _ = self._entry(stats, ["direction:out", f"topic:{forward_queue}", "type:rabbitmq"])
        _in2, in2_parent, _ = self._entry(stats, ["direction:in", f"topic:{forward_queue}", "type:rabbitmq"])
        assert in1_parent == out1
        assert out2_parent == in1
        assert in2_parent == out2

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_DATA_STREAMS_ENABLED="True", DD_PIKA_DISTRIBUTED_TRACING="false")
    )
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_dsm_without_distributed_tracing(self):
        self._publish()
        _method, properties, _body, _span = self._consume_one()
        assert PROPAGATION_KEY_BASE_64 in properties.headers
        assert "x-datadog-trace-id" not in properties.headers

        stats = self._pathway_stats()
        out_hash, _, _ = self._entry(
            stats, ["direction:out", f"exchange:{self.exchange}", "has_routing_key:true", "type:rabbitmq"]
        )
        _in_hash, in_parent, _ = self._entry(stats, ["direction:in", f"topic:{self.queue}", "type:rabbitmq"])
        assert in_parent == out_hash
