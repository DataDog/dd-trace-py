from contextlib import contextmanager

import pytest

import ddtrace
from ddtrace.debugging._exception import replay
from ddtrace.internal.packages import _third_party_packages
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.settings.exception_replay import ExceptionReplayConfig
from tests.debugging.mocking import exception_replay
from tests.utils import TracerTestCase


def test_exception_replay_config_enabled(monkeypatch):
    monkeypatch.setenv("DD_EXCEPTION_REPLAY_ENABLED", "1")

    er_config = ExceptionReplayConfig()
    assert er_config.enabled


def test_exception_replay_config_enabled_deprecated(monkeypatch):
    monkeypatch.setenv("DD_EXCEPTION_DEBUGGING_ENABLED", "1")

    er_config = ExceptionReplayConfig()
    assert er_config.enabled

    monkeypatch.setenv("DD_EXCEPTION_REPLAY_ENABLED", "false")

    er_config = ExceptionReplayConfig()
    assert not er_config.enabled


@contextmanager
def with_rate_limiter(limiter):
    original_limiter = replay.GLOBAL_RATE_LIMITER
    mocked = replay.GLOBAL_RATE_LIMITER = limiter

    yield mocked

    replay.GLOBAL_RATE_LIMITER = original_limiter


class ExceptionReplayTestCase(TracerTestCase):
    def setUp(self):
        super(ExceptionReplayTestCase, self).setUp()
        self.backup_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer
        _third_party_packages().remove("ddtrace")

    def tearDown(self):
        _third_party_packages().add("ddtrace")
        ddtrace.tracer = self.backup_tracer
        super(ExceptionReplayTestCase, self).tearDown()

    def test_debugger_exception_replay(self):
        def a(v, d=None):
            with self.trace("a"):
                if not v:
                    raise ValueError("hello", v)

        def b(bar):
            with self.trace("b"):
                m = 4
                a(bar % m)

        def c(foo=42):
            with self.trace("c"):
                sh = 3
                b(foo << sh)

        with exception_replay() as uploader:
            with with_rate_limiter(RateLimiter(limit_rate=1, raise_on_exceed=False)):
                with pytest.raises(ValueError):
                    c()

            self.assert_span_count(3)
            assert len(uploader.collector.queue) == 3

            snapshots = {str(s.uuid): s for s in uploader.collector.queue}

            for n, span in enumerate(self.spans):
                assert span.get_tag("error.debug_info_captured") == "true"

                exc_id = span.get_tag("_dd.debug.error.exception_id")

                info = {k: v for k, v in enumerate(["c", "b", "a"][n:], start=1)}

                for i in range(1, len(info) + 1):
                    fn = info[i]

                    # Check that we have all the tags for each snapshot
                    assert span.get_tag("_dd.debug.error.%d.snapshot_id" % i) in snapshots
                    assert span.get_tag("_dd.debug.error.%d.file" % i) == __file__.replace(".pyc", ".py"), span.get_tag(
                        "_dd.debug.error.%d.file" % i
                    )
                    assert span.get_tag("_dd.debug.error.%d.function" % i) == fn, "_dd.debug.error.%d.function = %s" % (
                        i,
                        span.get_tag("_dd.debug.error.%d.function" % i),
                    )
                    assert span.get_tag("_dd.debug.error.%d.line" % i), "_dd.debug.error.%d.line = %s" % (
                        i,
                        span.get_tag("_dd.debug.error.%d.line" % i),
                    )

                    assert all(str(s.exc_id) == exc_id for s in snapshots.values())

            # assert all spans use the same exc_id
            exc_ids = set(span.get_tag("_dd.debug.error.exception_id") for span in self.spans)
            assert len(exc_ids) == 1

    def test_debugger_exception_chaining(self):
        def a(v, d=None):
            with self.trace("a"):
                if not v:
                    raise ValueError("hello", v)

        def b_chain(bar):
            with self.trace("b"):
                m = 4
                try:
                    a(bar % m)
                except ValueError:
                    raise KeyError("chain it")

        def c(foo=42):
            with self.trace("c"):
                sh = 3
                b_chain(foo << sh)

        with exception_replay() as uploader:
            rate_limiter = RateLimiter(
                limit_rate=0.1,  # one trace per second
                tau=10,
                raise_on_exceed=False,
            )
            with with_rate_limiter(rate_limiter):
                with pytest.raises(KeyError):
                    c()

            self.assert_span_count(3)
            assert len(uploader.collector.queue) == 3

            snapshots = {str(s.uuid): s for s in uploader.collector.queue}

            stacks = [["b_chain", "a", "c", "b_chain"], ["b_chain", "a"], ["a"]]
            number_of_exc_ids = 1

            for n, span in enumerate(self.spans):
                assert span.get_tag("error.debug_info_captured") == "true"

                exc_id = span.get_tag("_dd.debug.error.exception_id")

                info = {k: v for k, v in enumerate(stacks[n], start=1)}

                for i in range(1, len(info) + 1):
                    fn = info[i]

                    # Check that we have all the tags for each snapshot
                    assert span.get_tag("_dd.debug.error.%d.snapshot_id" % i) in snapshots
                    assert span.get_tag("_dd.debug.error.%d.file" % i) == __file__.replace(".pyc", ".py"), span.get_tag(
                        "_dd.debug.error.%d.file" % i
                    )
                    assert span.get_tag("_dd.debug.error.%d.function" % i) == fn, "_dd.debug.error.%d.function = %s" % (
                        i,
                        span.get_tag("_dd.debug.error.%d.function" % i),
                    )
                    assert span.get_tag("_dd.debug.error.%d.line" % i), "_dd.debug.error.%d.line = %s" % (
                        i,
                        span.get_tag("_dd.debug.error.%d.line" % i),
                    )

                    # ensure we point to the right snapshots
                    assert any(str(s.exc_id) == exc_id for s in snapshots.values())

            # assert number of unique exc_ids based on python version
            exc_ids = set(span.get_tag("_dd.debug.error.exception_id") for span in self.spans)
            assert len(exc_ids) == number_of_exc_ids

            # invoke again (should be in less than 1 sec)
            with with_rate_limiter(rate_limiter):
                with pytest.raises(KeyError):
                    c()

            self.assert_span_count(6)
            # no new snapshots
            assert len(uploader.collector.queue) == 3
