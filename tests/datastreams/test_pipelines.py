"""Pipelines reporter recording semantics (DSM2-531).

Covers TEST-doc T-1 (sides), T-2 (consume -> produce link), T-3 (source links),
T-4 (consumed-context lifetime), T-10 (interning and determinism) and T-13
(the recording API never raises, including under threads), plus the
get_pipelines_reporter gating contract.
"""

import threading
from unittest import mock

from ddtrace.internal.datastreams import pipelines
from tests.utils import override_global_config


class FakeClock:
    """Deterministic monotonic clock for window tests."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _side_index(report, side):
    return {tuple(s): i for i, s in enumerate(report["sides"])}[tuple(side)]


# -- T-1: sides ------------------------------------------------------------


def test_t1_records_consume_and_produce_sides():
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    reporter.set_current_consume("sqs", "", "queue-1", "group-1")
    report = reporter.take_report("my-service", "prod")
    assert report == {
        "service": "my-service",
        "env": "prod",
        "sides": [["consume", "sqs", "", "queue-1", "group-1"]],
        "links": [],
    }
    # the window was reset: nothing more to report without new traffic
    assert reporter.take_report("my-service", "prod") is None


# -- T-2: consume -> produce links ------------------------------------------


def test_t2_produce_links_to_current_consume():
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    reporter.set_current_consume("kafka", "cluster-a", "payments", "billing")
    reporter.on_produce("sqs", "", "invoices")
    reporter.on_produce("sns", "", "notifications")
    report = reporter.take_report("svc", "")
    consume = _side_index(report, ("consume", "kafka", "cluster-a", "payments", "billing"))
    out_invoices = _side_index(report, ("produce", "sqs", "", "invoices", ""))
    out_notifications = _side_index(report, ("produce", "sns", "", "notifications", ""))
    assert report["links"] == sorted([[consume, out_invoices], [consume, out_notifications]])


# -- T-3: produce without a consume parent is a source link -----------------


def test_t3_produce_without_consume_parent_is_source():
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    reporter.on_produce("kafka", "", "orders")
    report = reporter.take_report("svc", "")
    assert report["sides"] == [["produce", "kafka", "", "orders", ""]]
    assert report["links"] == [[-1, 0]]


def test_t3_produce_on_other_thread_is_source():
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    reporter.set_current_consume("kafka", "", "payments", "g")
    thread = threading.Thread(target=reporter.on_produce, args=("sqs", "", "out"))
    thread.start()
    thread.join()
    report = reporter.take_report("svc", "")
    out = _side_index(report, ("produce", "sqs", "", "out", ""))
    assert report["links"] == [[-1, out]]


# -- T-4: consumed-context lifetime -----------------------------------------


def test_t4_consume_context_replaced_by_next_consume():
    # The in-process rule is classic DSM's exactly: the current consumed
    # message is replaced by the next consume on the thread and is never
    # cleared otherwise (there is no scope-exit clear path).
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    reporter.set_current_consume("kafka", "", "topic-1", "g")
    reporter.set_current_consume("kafka", "", "topic-2", "g")
    # topic-2 replaced topic-1 as the thread's current consumed message
    reporter.on_produce("sqs", "", "after-replace")
    # no consume since: the context is still topic-2, the produce links to it
    reporter.on_produce("sqs", "", "no-replace-since")
    # a brand-new consume replaces it again
    reporter.set_current_consume("kafka", "", "topic-4", "g")
    reporter.on_produce("sqs", "", "after-second-replace")

    report = reporter.take_report("svc", "")
    links = {tuple(link) for link in report["links"]}
    assert (
        _side_index(report, ("consume", "kafka", "", "topic-2", "g")),
        _side_index(report, ("produce", "sqs", "", "after-replace", "")),
    ) in links
    assert (
        _side_index(report, ("consume", "kafka", "", "topic-2", "g")),
        _side_index(report, ("produce", "sqs", "", "no-replace-since", "")),
    ) in links
    assert (
        _side_index(report, ("consume", "kafka", "", "topic-4", "g")),
        _side_index(report, ("produce", "sqs", "", "after-second-replace", "")),
    ) in links


# -- T-10: interning and determinism -----------------------------------------


def test_t10_interning_and_deterministic_order():
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    # SQS-style batch: repeated consumes of the same side intern to one side
    for _ in range(5):
        reporter.set_current_consume("sqs", "", "queue", "batch-1")
    for _ in range(5):
        reporter.on_produce("kafka", "", "out")
    report = reporter.take_report("svc", "")
    assert len(report["sides"]) == 2
    assert report["sides"] == sorted(report["sides"])
    consume = _side_index(report, ("consume", "sqs", "", "queue", "batch-1"))
    out = _side_index(report, ("produce", "kafka", "", "out", ""))
    assert report["links"] == [[consume, out]]

    # the same event sequence always produces the same report bytes
    twin = pipelines.PipelinesReporter(clock=FakeClock())
    for _ in range(5):
        twin.set_current_consume("sqs", "", "queue", "batch-1")
    for _ in range(5):
        twin.on_produce("kafka", "", "out")
    assert pipelines.packb(twin.take_report("svc", "")) == pipelines.packb(report)


# -- T-13: the recording API never raises, including under threads ------------


def test_t13_never_raises_with_garbage_arguments_under_threads():
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    errors = []

    def hammer(worker):
        try:
            for _ in range(200):
                # unhashable / wrong-typed arguments must be swallowed
                reporter.set_current_consume("kafka", None, {"bad": "topic"}, 3)
                reporter.on_produce(None, 42, None)
                reporter.set_current_consume("sqs", [1], object(), None)
                reporter.set_current_consume("kombu", "", "exchange-%d" % worker, "")
                reporter.on_produce("rabbitmq", "", "queue-%d" % worker)
                # concurrent window taking must be safe
                reporter.take_report("svc", "", due=True)
        except BaseException as e:  # noqa: BLE001 - the test asserts nothing escapes
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors

    # the reporter is still fully functional after the hammering
    fresh = pipelines.PipelinesReporter(clock=FakeClock())
    fresh.set_current_consume("kafka", "c", "t", "g")
    fresh.on_produce("sqs", "", "out")
    report = fresh.take_report("svc", "")
    consume = _side_index(report, ("consume", "kafka", "c", "t", "g"))
    out = _side_index(report, ("produce", "sqs", "", "out", ""))
    assert report["links"] == [[consume, out]]


# -- get_pipelines_reporter gating --------------------------------------------


def test_get_pipelines_reporter_disabled_returns_none():
    with mock.patch.object(pipelines.config, "_data_streams_pipelines_enabled", False):
        assert pipelines.get_pipelines_reporter() is None


def test_get_pipelines_reporter_none_when_apm_tracing_off():
    with override_global_config(dict(_tracing_enabled=False)):
        assert pipelines.get_pipelines_reporter() is None


def test_get_pipelines_reporter_is_singleton_with_lazy_lifecycle():
    with mock.patch.object(pipelines.config, "_data_streams_enabled", False):
        reporter = pipelines.get_pipelines_reporter()
        try:
            assert reporter is not None
            assert pipelines.get_pipelines_reporter() is reporter
            # lazy lifecycle: no flush thread / atexit hook before the first side
            assert pipelines._flusher is None
            assert not pipelines._lifecycle_started
            reporter.on_produce("kafka", "", "orders")
            assert pipelines._lifecycle_started
            assert pipelines._flusher is not None
        finally:
            if pipelines._flusher is not None:
                pipelines._flusher.stop(timeout=2)
