"""Tests for the automatic DSM pipelines botocore hooks (DSM2-531, TEST-doc T-5).

These tests drive the pipelines recording handlers in
ddtrace/internal/datastreams/botocore.py directly (and a few through
ddtrace.internal.core.dispatch to validate the subscriptions) with the same
fake params/result objects the other tests/datastreams tests use, then assert
the recorded sides/links via the pinned recording API in
ddtrace.internal.datastreams.pipelines: build a PipelinesReporter through
get_pipelines_reporter(), record, and inspect take_report("svc", "env"),
which returns the report dict and resets the window (CONTRACT.md §1).
"""

import json
import threading

import pytest

from ddtrace.internal import core
import ddtrace.internal.datastreams.botocore as botocore_dsm
from ddtrace.internal.datastreams.botocore import classify_sqs_message_origin
from ddtrace.internal.datastreams.botocore import handle_pipelines_api_call_started
from ddtrace.internal.datastreams.botocore import handle_pipelines_eventbridge_produce
from ddtrace.internal.datastreams.botocore import handle_pipelines_kinesis_produce
from ddtrace.internal.datastreams.botocore import handle_pipelines_kinesis_receive
from ddtrace.internal.datastreams.botocore import handle_pipelines_sqs_receive
from ddtrace.internal.datastreams.botocore import handle_pipelines_sqs_sns_produce
import ddtrace.internal.datastreams.pipelines as pipelines


QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue"
QUEUE_URL_2 = "https://sqs.us-east-1.amazonaws.com/123456789012/other-queue"
TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:my-topic"

SNS_ENVELOPE_BODY = json.dumps(
    {
        "Type": "Notification",
        "MessageId": "95df01b0-ee17-5a51-829c-3example",
        "TopicArn": TOPIC_ARN,
        "Message": "hello",
        "Timestamp": "2024-01-01T00:00:00.000Z",
    }
)
EVENTBRIDGE_ENVELOPE_BODY = json.dumps(
    {
        "version": "0",
        "id": "6a7e8feb-b491-4cf7-a9f1-9example",
        "detail-type": "order-created",
        "source": "aws.events",
        "detail": {"orderId": "123"},
    }
)


@pytest.fixture(autouse=True)
def _fresh_pipelines_reporter():
    """Isolate each test: a fresh reporter (window + per-thread consume contexts)
    and an empty per-queue origin classification cache.
    """
    pipelines._reporter = None
    botocore_dsm._sqs_queue_origins.clear()
    yield
    pipelines._reporter = None


def _take_report():
    reporter = pipelines.get_pipelines_reporter()
    assert reporter is not None
    return reporter.take_report("svc", "env") or {"sides": [], "links": []}


def _assert_side(report, side):
    assert list(side) in report["sides"]


def _assert_no_side(report, side):
    assert list(side) not in report["sides"]


def _assert_link(report, in_side, out_side):
    sides = [tuple(s) for s in report["sides"]]
    links = [tuple(link) for link in report["links"]]
    in_idx = -1 if in_side is None else sides.index(tuple(in_side))
    assert (in_idx, sides.index(tuple(out_side))) in links


def _sqs_receive_result(bodies):
    return {"Messages": [{"MessageId": str(i), "Body": body} for i, body in enumerate(bodies)]}


class TestProduceSides:
    def test_sqs_send_message_records_produce_side(self):
        handle_pipelines_sqs_sns_produce(None, None, "sqs", {}, {"QueueUrl": QUEUE_URL})

        report = _take_report()
        assert report["service"] == "svc"
        assert report["env"] == "env"
        _assert_side(report, (0, "sqs", "", "my-queue", ""))

    def test_sns_publish_records_produce_side(self):
        handle_pipelines_sqs_sns_produce(None, None, "sns", {}, {"TopicArn": TOPIC_ARN})

        report = _take_report()
        _assert_side(report, (0, "sns", "", TOPIC_ARN, ""))

    def test_sqs_sns_batch_entries_intern_to_one_side(self):
        # The update_messages event fires once per batch entry with the same
        # queue-level QueueUrl; the reporter interns identical sides.
        params = {"QueueUrl": QUEUE_URL}
        for _ in range(3):
            handle_pipelines_sqs_sns_produce(None, None, "sqs", {}, params, {"MessageBody": "x"})

        report = _take_report()
        assert report["sides"] == [[0, "sqs", "", "my-queue", ""]]

    def test_kinesis_put_record_records_produce_side(self):
        handle_pipelines_kinesis_produce(None, "my-stream", {}, {"Data": "{}", "PartitionKey": "k"})

        report = _take_report()
        _assert_side(report, (0, "kinesis", "", "my-stream", ""))

    def test_kinesis_produce_without_stream_records_nothing(self):
        # Neither StreamName nor StreamARN is a guaranteed param.
        handle_pipelines_kinesis_produce(None, "", {}, {"Data": "{}", "PartitionKey": "k"})

        assert _take_report()["sides"] == []

    def test_eventbridge_put_events_records_produce_side(self):
        entry = {"DetailType": "order-created", "Detail": "{}"}
        handle_pipelines_eventbridge_produce(None, None, None, {}, {"Entries": [entry]}, entry)

        report = _take_report()
        _assert_side(report, (0, "eventbridge", "", "order-created", ""))

    def test_eventbridge_event_without_entry_is_noop(self):
        # With DD_DATA_STREAMS_ENABLED=false the contrib strips params and the
        # entry from the event payload; this handler cannot derive a side from
        # it (that case is covered by the api-call-started fallback).
        handle_pipelines_eventbridge_produce(None, None, None, {}, None, None)

        assert _take_report()["sides"] == []

    def test_eventbridge_api_call_started_fallback_records_sides(self):
        ctx = core.context_with_data(
            "botocore.patched_api_call",
            endpoint_name="events",
            operation="PutEvents",
            params={
                "Entries": [
                    {"DetailType": "order-created"},
                    {"DetailType": "order-shipped"},
                ]
            },
        )

        handle_pipelines_api_call_started(ctx)

        report = _take_report()
        _assert_side(report, (0, "eventbridge", "", "order-created", ""))
        _assert_side(report, (0, "eventbridge", "", "order-shipped", ""))

    def test_api_call_started_ignores_non_events_calls(self):
        ctx = core.context_with_data(
            "botocore.patched_api_call",
            endpoint_name="sns",
            operation="Publish",
            params={"TopicArn": TOPIC_ARN},
        )

        handle_pipelines_api_call_started(ctx)

        assert _take_report()["sides"] == []


class TestConsumeSides:
    def test_sqs_receive_records_consume_side(self):
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result(["hello"]))

        report = _take_report()
        _assert_side(report, (1, "sqs", "", "my-queue", ""))

    def test_sqs_receive_sns_envelope_classified_as_sns_topic(self):
        # The SNS classification is what makes the SNS -> SQS topology a single
        # pipeline: the subscriber's consume side matches the publisher's
        # produce side on (queue_type, topic) = ("sns", TopicArn).
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result([SNS_ENVELOPE_BODY]))

        report = _take_report()
        _assert_side(report, (1, "sns", "", TOPIC_ARN, ""))
        _assert_no_side(report, (1, "sqs", "", "my-queue", ""))

    def test_sqs_receive_eventbridge_envelope_classified_by_detail_type(self):
        handle_pipelines_sqs_receive(
            None,
            {"QueueUrl": QUEUE_URL},
            _sqs_receive_result([EVENTBRIDGE_ENVELOPE_BODY]),
        )

        report = _take_report()
        _assert_side(report, (1, "eventbridge", "", "order-created", ""))
        _assert_no_side(report, (1, "sqs", "", "my-queue", ""))

    def test_sqs_receive_empty_poll_records_nothing(self):
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, {})

        assert _take_report()["sides"] == []

    def test_sqs_receive_batch_interns_one_side(self):
        # T-5 batch: all messages of one queue are the same side, recorded once.
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result(["m1", "m2", "m3"]))

        report = _take_report()
        assert report["sides"] == [[1, "sqs", "", "my-queue", ""]]

    def test_sqs_receive_batch_last_message_side_wins_context(self):
        # A mixed batch records every message's own side, but only the LAST
        # message's side stays current for the thread: a following produce
        # links to it.
        handle_pipelines_sqs_receive(
            None,
            {"QueueUrl": QUEUE_URL},
            _sqs_receive_result([SNS_ENVELOPE_BODY, "plain"]),
        )

        handle_pipelines_sqs_sns_produce(None, None, "sns", {}, {"TopicArn": "arn:aws:sns:us-east-1:1:out"})

        report = _take_report()
        _assert_side(report, (1, "sns", "", TOPIC_ARN, ""))
        _assert_side(report, (1, "sqs", "", "my-queue", ""))
        _assert_link(
            report,
            (1, "sqs", "", "my-queue", ""),
            (0, "sns", "", "arn:aws:sns:us-east-1:1:out", ""),
        )

    def test_sqs_origin_verdict_cached_per_queue(self, monkeypatch):
        # Positive origin verdicts are cached per queue: a marker-carrying body
        # on an already-classified queue reuses the verdict without re-parsing.
        calls = []
        real_classify = botocore_dsm.classify_sqs_message_origin

        def classify_spy(body):
            calls.append(body)
            return real_classify(body)

        monkeypatch.setattr(botocore_dsm, "classify_sqs_message_origin", classify_spy)

        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result([SNS_ENVELOPE_BODY]))
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result([SNS_ENVELOPE_BODY]))

        report = _take_report()
        assert report["sides"] == [[1, "sns", "", TOPIC_ARN, ""]]
        assert len(calls) == 1

    def test_marker_free_body_on_classified_queue_is_attributed_to_queue(self, monkeypatch):
        # The cheap substring check still runs for every message: a body
        # without envelope markers is attributed to the queue itself even
        # after the queue got a positive origin verdict, so a mixed batch
        # keeps per-message sides.
        calls = []
        real_classify = botocore_dsm.classify_sqs_message_origin

        def classify_spy(body):
            calls.append(body)
            return real_classify(body)

        monkeypatch.setattr(botocore_dsm, "classify_sqs_message_origin", classify_spy)

        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result([SNS_ENVELOPE_BODY]))
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result(["plain body"]))

        report = _take_report()
        _assert_side(report, (1, "sns", "", TOPIC_ARN, ""))
        _assert_side(report, (1, "sqs", "", "my-queue", ""))
        # the plain body never reaches the JSON classification
        assert calls == [SNS_ENVELOPE_BODY]

    def test_unclassified_queue_still_substring_checks_each_message(self):
        # Plain-SQS verdicts are not cached: the cheap substring check runs for
        # every message of an unclassified queue, so an SNS envelope arriving
        # later on the same queue is still detected.
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result(["plain"]))
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result([SNS_ENVELOPE_BODY]))

        report = _take_report()
        _assert_side(report, (1, "sqs", "", "my-queue", ""))
        _assert_side(report, (1, "sns", "", TOPIC_ARN, ""))

    def test_origin_cache_is_per_queue(self):
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result([SNS_ENVELOPE_BODY]))
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL_2}, _sqs_receive_result(["plain"]))

        report = _take_report()
        assert report["sides"] == [
            [1, "sns", "", TOPIC_ARN, ""],
            [1, "sqs", "", "other-queue", ""],
        ]

    def test_kinesis_get_records_records_consume_side(self):
        handle_pipelines_kinesis_receive(None, {"StreamName": "my-stream"}, 0.0, None, {"Data": "{}"})

        report = _take_report()
        _assert_side(report, (1, "kinesis", "", "my-stream", ""))

    def test_kinesis_receive_without_stream_records_nothing(self):
        handle_pipelines_kinesis_receive(None, {}, 0.0, None, {"Data": "{}"})

        assert _take_report()["sides"] == []


class TestClassification:
    def test_sns_envelope_body(self):
        assert classify_sqs_message_origin(SNS_ENVELOPE_BODY) == ("sns", TOPIC_ARN)

    def test_eventbridge_envelope_body(self):
        assert classify_sqs_message_origin(EVENTBRIDGE_ENVELOPE_BODY) == (
            "eventbridge",
            "order-created",
        )

    def test_plain_body_is_not_classified(self):
        assert classify_sqs_message_origin("plain body") is None
        assert classify_sqs_message_origin(json.dumps({"job_type": "TAX"})) is None

    def test_marker_without_valid_envelope_is_not_classified(self):
        assert classify_sqs_message_origin("TopicArn but not json") is None
        assert classify_sqs_message_origin('{"detail-type": 12}') is None
        assert classify_sqs_message_origin("[1, 2]") is None
        assert classify_sqs_message_origin("") is None


class TestLinks:
    def test_produce_inside_consume_context_links_sides(self):
        # T-3 semantics: a produce on the same thread as a current consume
        # context links the consume side to the produce side.
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result(["hello"]))
        handle_pipelines_sqs_sns_produce(None, None, "sns", {}, {"TopicArn": "arn:aws:sns:us-east-1:1:out"})

        report = _take_report()
        _assert_link(
            report,
            (1, "sqs", "", "my-queue", ""),
            (0, "sns", "", "arn:aws:sns:us-east-1:1:out", ""),
        )

    def test_produce_without_consume_context_is_source(self):
        handle_pipelines_sqs_sns_produce(None, None, "sqs", {}, {"QueueUrl": QUEUE_URL})

        report = _take_report()
        _assert_link(report, None, (0, "sqs", "", "my-queue", ""))

    def test_produce_on_another_thread_is_source(self):
        # The current consume context is per-thread: a produce on a thread with
        # no consume context records a source link (in_side = -1), while a
        # produce on the consuming thread still links.
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result(["hello"]))

        def produce_on_other_thread():
            handle_pipelines_sqs_sns_produce(None, None, "sns", {}, {"TopicArn": "arn:aws:sns:us-east-1:1:out"})

        t = threading.Thread(target=produce_on_other_thread)
        t.start()
        t.join()

        handle_pipelines_sqs_sns_produce(None, None, "sqs", {}, {"QueueUrl": QUEUE_URL_2})

        report = _take_report()
        _assert_link(report, None, (0, "sns", "", "arn:aws:sns:us-east-1:1:out", ""))
        _assert_link(report, (1, "sqs", "", "my-queue", ""), (0, "sqs", "", "other-queue", ""))

    def test_current_consume_context_replaced_by_next_consume(self):
        # T-4 semantics: the consumed message's context is gone once replaced
        # by a later consume on the same thread; a following produce links to
        # the newer side only.
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL}, _sqs_receive_result(["hello"]))
        handle_pipelines_sqs_receive(None, {"QueueUrl": QUEUE_URL_2}, _sqs_receive_result(["hello"]))
        handle_pipelines_sqs_sns_produce(None, None, "sqs", {}, {"QueueUrl": QUEUE_URL})

        report = _take_report()
        _assert_side(report, (1, "sqs", "", "my-queue", ""))
        _assert_side(report, (1, "sqs", "", "other-queue", ""))
        _assert_link(report, (1, "sqs", "", "other-queue", ""), (0, "sqs", "", "my-queue", ""))
        links = [tuple(link) for link in report["links"]]
        assert len(links) == 1


class TestSubscriptions:
    """The handlers are subscribed at module import, unconditionally."""

    def test_sqs_sns_update_messages_event_records_produce_side(self):
        core.dispatch(
            "botocore.sqs_sns.update_messages",
            (None, None, "sqs", {}, {"QueueUrl": QUEUE_URL}),
        )

        report = _take_report()
        _assert_side(report, (0, "sqs", "", "my-queue", ""))

    def test_kinesis_update_record_event_records_produce_side(self):
        core.dispatch(
            "botocore.kinesis.update_record",
            (None, "my-stream", {}, {"Data": "{}"}, True),
        )

        report = _take_report()
        _assert_side(report, (0, "kinesis", "", "my-stream", ""))

    def test_sqs_receive_message_post_event_records_consume_side(self):
        ctx = core.context_with_data("botocore.sqs.ReceiveMessage")
        core.dispatch(
            "botocore.sqs.ReceiveMessage.post",
            (
                ctx,
                {"QueueUrl": QUEUE_URL},
                _sqs_receive_result([SNS_ENVELOPE_BODY]),
                False,
                None,
            ),
        )

        report = _take_report()
        _assert_side(report, (1, "sns", "", TOPIC_ARN, ""))

    def test_kinesis_get_records_post_event_records_consume_side(self):
        ctx = core.context_with_data("botocore.kinesis.GetRecords")
        core.dispatch(
            "botocore.kinesis.GetRecords.post",
            (
                ctx,
                {"StreamName": "my-stream"},
                0.0,
                None,
                {"Data": "{}"},
                {},
                False,
                None,
            ),
        )

        report = _take_report()
        _assert_side(report, (1, "kinesis", "", "my-stream", ""))

    def test_eventbridge_api_call_started_event_records_produce_side(self):
        from ddtrace.trace import tracer

        # Give the context a (non-activated) span so the tracer's own listener
        # for this event does not create a fallback span that would leak onto
        # the test's context provider. It is left unfinished on purpose: the
        # local native .so skew breaks span finishing (see the baseline
        # failure list), and nothing here needs it finished.
        span = tracer.start_span("test")
        ctx = core.context_with_data(
            "botocore.patched_api_call",
            endpoint_name="events",
            operation="PutEvents",
            params={"Entries": [{"DetailType": "order-created"}]},
            _inner_span=span,
        )
        core.dispatch("botocore.patched_api_call.started", (ctx,))

        report = _take_report()
        _assert_side(report, (0, "eventbridge", "", "order-created", ""))


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_PIPELINES_ENABLED": "false"}, err=None)
def test_handlers_noop_when_pipelines_reporting_disabled():
    from ddtrace.internal.datastreams.botocore import handle_pipelines_eventbridge_produce
    from ddtrace.internal.datastreams.botocore import handle_pipelines_kinesis_produce
    from ddtrace.internal.datastreams.botocore import handle_pipelines_kinesis_receive
    from ddtrace.internal.datastreams.botocore import handle_pipelines_sqs_receive
    from ddtrace.internal.datastreams.botocore import handle_pipelines_sqs_sns_produce
    import ddtrace.internal.datastreams.pipelines as pipelines

    # The kill switch makes every handler a no-op (and they never raise).
    assert pipelines.get_pipelines_reporter() is None
    handle_pipelines_sqs_sns_produce(None, None, "sqs", {}, {"QueueUrl": "https://sqs.us-east-1.amazonaws.com/1/q"})
    handle_pipelines_sqs_sns_produce(None, None, "sns", {}, {"TopicArn": "arn:aws:sns:us-east-1:1:t"})
    handle_pipelines_kinesis_produce(None, "my-stream", {}, {})
    handle_pipelines_kinesis_receive(None, {"StreamName": "my-stream"}, 0.0, None, {})
    handle_pipelines_eventbridge_produce(None, None, None, {}, {"Entries": [{"DetailType": "d"}]}, {"DetailType": "d"})
    handle_pipelines_sqs_receive(
        None,
        {"QueueUrl": "https://sqs.us-east-1.amazonaws.com/1/q"},
        {"Messages": [{"Body": "x"}]},
    )
    assert pipelines.get_pipelines_reporter() is None
