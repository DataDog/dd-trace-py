"""Tests for the automatic DSM pipelines google-cloud-pubsub hooks (DSM2-531).

Covers TEST doc T-5 (pub/sub sides and in-process links), plus the T-3 (produce
links to the thread's current consume) and T-4 (context replaced by the next
consume) semantics where natural. Handlers are driven directly and through
ddtrace.internal.core.dispatch with the same fake objects the existing
datastreams tests use.
"""

import threading

import pytest

import ddtrace.internal.datastreams.google_cloud_pubsub as pubsub_hooks
from ddtrace.internal.datastreams.google_cloud_pubsub import pipelines_pubsub_receive
from ddtrace.internal.datastreams.google_cloud_pubsub import pipelines_pubsub_send
from ddtrace.internal.datastreams.google_cloud_pubsub import pipelines_pubsub_subscription_result
import ddtrace.internal.datastreams.pipelines as pipelines_module
from ddtrace.internal.datastreams.pipelines import PipelinesReporter


TOPIC_PATH = "projects/test-project/topics/test-topic"
SUBSCRIPTION_PATH = "projects/test-project/subscriptions/test-subscription"
OTHER_SUBSCRIPTION_PATH = "projects/test-project/subscriptions/other-subscription"
OTHER_TOPIC_PATH = "projects/test-project/topics/other-topic"


class FakeSubscription:
    """Proto-like result of a create/get/update subscription admin call."""

    def __init__(self, name, topic):
        self.name = name
        self.topic = topic


class FakeMessage:
    def __init__(self, data=b"body", attributes=None):
        self.data = data
        self.attributes = attributes or {}


@pytest.fixture
def reporter(monkeypatch):
    # Fresh process-level reporter and an empty subscription -> topic cache so
    # every test starts from a clean window.
    fresh = PipelinesReporter()
    monkeypatch.setattr(pipelines_module, "_reporter", fresh)
    monkeypatch.setattr(pubsub_hooks, "_subscription_topic_cache", {})
    return fresh


def test_produce_side_values(reporter):
    # T-5: the topic is the publish topic argument exactly as passed (full path).
    pipelines_pubsub_send((TOPIC_PATH,), {}, None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [[0, "pubsub", "", TOPIC_PATH, ""]]
    # No current consume on this thread: the produce is a source link.
    assert report["links"] == [[-1, 0]]


def test_produce_side_values_topic_as_kwarg(reporter):
    pipelines_pubsub_send((), {"topic": TOPIC_PATH}, None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [[0, "pubsub", "", TOPIC_PATH, ""]]
    assert report["links"] == [[-1, 0]]


def test_produce_skipped_without_topic(reporter):
    pipelines_pubsub_send((), {}, None)

    assert reporter.take_report("svc", "env") is None


def test_consume_side_with_cached_topic(reporter):
    # T-5: the subscription -> topic cache maps the subscription path to its
    # bound topic; the group is always the subscription path.
    pipelines_pubsub_subscription_result(FakeSubscription(SUBSCRIPTION_PATH, TOPIC_PATH))
    pipelines_pubsub_receive(SUBSCRIPTION_PATH, FakeMessage(), None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [[1, "pubsub", "", TOPIC_PATH, SUBSCRIPTION_PATH]]
    assert report["links"] == []


def test_consume_side_without_cache_falls_back_to_subscription_path(reporter):
    # T-5 documented fallback: without a cached bound topic the subscription
    # path itself is the topic.
    pipelines_pubsub_receive(SUBSCRIPTION_PATH, FakeMessage(), None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [[1, "pubsub", "", SUBSCRIPTION_PATH, SUBSCRIPTION_PATH]]
    assert report["links"] == []


def test_subscription_result_accepts_dict(reporter):
    pipelines_pubsub_subscription_result({"name": SUBSCRIPTION_PATH, "topic": TOPIC_PATH})
    pipelines_pubsub_receive(SUBSCRIPTION_PATH, FakeMessage(), None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [[1, "pubsub", "", TOPIC_PATH, SUBSCRIPTION_PATH]]


def test_subscription_result_with_missing_or_empty_fields_is_noop(reporter):
    # Bad admin results must never raise and must not pollute the cache.
    pipelines_pubsub_subscription_result(None)
    pipelines_pubsub_subscription_result(object())
    pipelines_pubsub_subscription_result({"name": SUBSCRIPTION_PATH})
    pipelines_pubsub_subscription_result({"name": "", "topic": TOPIC_PATH})

    pipelines_pubsub_receive(SUBSCRIPTION_PATH, FakeMessage(), None)
    report = reporter.take_report("svc", "env")
    # No cache entry: the fallback topic is the subscription path.
    assert report["sides"] == [[1, "pubsub", "", SUBSCRIPTION_PATH, SUBSCRIPTION_PATH]]


def test_link_produce_inside_consume_context(reporter):
    # T-3: a produce on the same thread after a consume links to that consume.
    pipelines_pubsub_subscription_result(FakeSubscription(SUBSCRIPTION_PATH, TOPIC_PATH))
    pipelines_pubsub_receive(SUBSCRIPTION_PATH, FakeMessage(), None)
    pipelines_pubsub_send((OTHER_TOPIC_PATH,), {}, None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [
        [0, "pubsub", "", OTHER_TOPIC_PATH, ""],
        [1, "pubsub", "", TOPIC_PATH, SUBSCRIPTION_PATH],
    ]
    assert report["links"] == [[1, 0]]


def test_consume_context_replaced_by_next_consume(reporter):
    # T-4: the consumed message's context is gone once a later consume replaces it.
    pipelines_pubsub_subscription_result(FakeSubscription(SUBSCRIPTION_PATH, TOPIC_PATH))
    pipelines_pubsub_receive(SUBSCRIPTION_PATH, FakeMessage(), None)
    pipelines_pubsub_receive(OTHER_SUBSCRIPTION_PATH, FakeMessage(), None)
    pipelines_pubsub_send((OTHER_TOPIC_PATH,), {}, None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [
        [0, "pubsub", "", OTHER_TOPIC_PATH, ""],
        [1, "pubsub", "", OTHER_SUBSCRIPTION_PATH, OTHER_SUBSCRIPTION_PATH],
        [1, "pubsub", "", TOPIC_PATH, SUBSCRIPTION_PATH],
    ]
    # Only the newer consume (uncached, subscription-path fallback) is the in-side.
    assert report["links"] == [[1, 0]]


def test_produce_on_other_thread_is_source(reporter):
    # T-3: the consume context is thread-local; a produce on another thread is a source.
    pipelines_pubsub_subscription_result(FakeSubscription(SUBSCRIPTION_PATH, TOPIC_PATH))
    pipelines_pubsub_receive(SUBSCRIPTION_PATH, FakeMessage(), None)

    errors = []

    def produce_on_other_thread():
        try:
            pipelines_pubsub_send((OTHER_TOPIC_PATH,), {}, None)
        except Exception as e:  # noqa: BLE001 - surface into the test thread
            errors.append(e)

    t = threading.Thread(target=produce_on_other_thread)
    t.start()
    t.join()
    assert not errors

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [
        [0, "pubsub", "", OTHER_TOPIC_PATH, ""],
        [1, "pubsub", "", TOPIC_PATH, SUBSCRIPTION_PATH],
    ]
    assert report["links"] == [[-1, 0]]


def test_handlers_never_raise(reporter):
    # T-13-ish safety: malformed args must not raise into application code.
    pipelines_pubsub_send((), {}, None)
    pipelines_pubsub_receive(None, None, None)

    assert reporter.take_report("svc", "env") is None


def test_handlers_noop_when_feature_disabled(monkeypatch):
    # Kill switch: get_pipelines_reporter() returns None and the handlers do nothing.
    monkeypatch.setattr(pipelines_module, "_pipelines_enabled", lambda: False)
    monkeypatch.setattr(pipelines_module, "_reporter", None)

    pipelines_pubsub_send((TOPIC_PATH,), {}, None)
    pipelines_pubsub_receive(SUBSCRIPTION_PATH, FakeMessage(), None)
    pipelines_pubsub_subscription_result(FakeSubscription(SUBSCRIPTION_PATH, TOPIC_PATH))

    assert pipelines_module.get_pipelines_reporter() is None


def test_handlers_subscribed_via_core_dispatch(reporter):
    # Wiring: the handlers fire on the contrib patch events, unconditionally at import.
    from ddtrace.internal import core

    core.dispatch("google_cloud_pubsub.send.pre", ((TOPIC_PATH,), {}, None))
    core.dispatch(
        "google_cloud_pubsub.receive.pre",
        (SUBSCRIPTION_PATH, FakeMessage(), None),
    )

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [
        [0, "pubsub", "", TOPIC_PATH, ""],
        [1, "pubsub", "", SUBSCRIPTION_PATH, SUBSCRIPTION_PATH],
    ]
    # The dispatch order is send then receive, so the produce has no consume parent.
    assert report["links"] == [[-1, 0]]


def test_subscription_result_event_subscribed_via_core_dispatch(reporter):
    from ddtrace.internal import core

    core.dispatch(
        "google_cloud_pubsub.subscription.result",
        (FakeSubscription(SUBSCRIPTION_PATH, TOPIC_PATH),),
    )

    assert pubsub_hooks._subscription_topic_cache[SUBSCRIPTION_PATH] == TOPIC_PATH


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_PIPELINES_ENABLED": "false"})
def test_disabled_via_env_subprocess():
    from ddtrace.internal import core
    from ddtrace.internal.datastreams import google_cloud_pubsub as pubsub_hooks  # noqa: F401  (registers hooks)
    from ddtrace.internal.datastreams.pipelines import get_pipelines_reporter

    assert get_pipelines_reporter() is None

    # Dispatching the events must be a no-op and never raise.
    core.dispatch("google_cloud_pubsub.send.pre", (("projects/p/topics/t",), {}, None))
    core.dispatch(
        "google_cloud_pubsub.receive.pre",
        ("projects/p/subscriptions/s", None, None),
    )


def test_contrib_admin_wrapper_dispatches_subscription_result(reporter):
    # The minimal contrib emission: get/create/update subscription admin calls
    # dispatch their Subscription result, which feeds the cache.
    pytest.importorskip("google.cloud.pubsub_v1")
    from ddtrace.contrib.internal.google_cloud_pubsub.patch import _make_admin_wrapper

    def fake_get_subscription(request=None, **kwargs):
        return FakeSubscription(SUBSCRIPTION_PATH, TOPIC_PATH)

    wrapper = _make_admin_wrapper("getSubscription", ["subscription"])
    wrapper(fake_get_subscription, None, ({"subscription": SUBSCRIPTION_PATH},), {})

    assert pubsub_hooks._subscription_topic_cache[SUBSCRIPTION_PATH] == TOPIC_PATH

    # Non-subscription admin results are not dispatched: the wrapper still runs fine.
    wrapper = _make_admin_wrapper("listSubscriptions", ["project"])
    assert wrapper(lambda request=None, **kwargs: object(), None, ({"project": "p"},), {}) is not None
    assert SUBSCRIPTION_PATH in pubsub_hooks._subscription_topic_cache
