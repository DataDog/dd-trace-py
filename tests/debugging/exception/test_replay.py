from collections import Counter
from contextlib import contextmanager
import sys

import pytest

import ddtrace
from ddtrace.debugging._exception import replay
from ddtrace.internal.packages import _third_party_packages
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.settings.exception_replay import ExceptionReplayConfig
from tests.debugging.mocking import exception_replay
from tests.utils import TracerTestCase
from tests.utils import override_third_party_packages


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


def test_exception_chain_ident():
    def a(v, d=None):
        if not v:
            raise ValueError("hello", v)

    def b_chain(bar):
        m = 4
        try:
            a(bar % m)
        except ValueError:
            raise KeyError("chain it")

    def c(foo=42):
        sh = 3
        b_chain(foo << sh)

    exc_idents = Counter()

    for _ in range(100):
        try:
            c()
        except Exception as e:
            chain, _ = replay.unwind_exception_chain(e, e.__traceback__)
            exc_idents[replay.exception_chain_ident(chain)] += 1

    assert len(exc_idents) == 1

    # This generates a different exception chain since c is called at a
    # different line number
    for _ in range(100):
        try:
            c()
        except Exception as e:
            chain, _ = replay.unwind_exception_chain(e, e.__traceback__)
            exc_idents[replay.exception_chain_ident(chain)] += 1

    assert len(exc_idents) == 2
    assert all(v == 100 for v in exc_idents.values())


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
                assert span.get_tag(replay.DEBUG_INFO_TAG) == "true"

                exc_id = span.get_tag(replay.EXCEPTION_ID_TAG)

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
            exc_ids = set(span.get_tag(replay.EXCEPTION_ID_TAG) for span in self.spans)
            assert None not in exc_ids and len(exc_ids) == 1

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
                except ValueError as exc:
                    raise KeyError("chain it") from exc

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
                assert span.get_tag(replay.DEBUG_INFO_TAG) == "true"

                exc_id = span.get_tag(replay.EXCEPTION_ID_TAG)

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
            exc_ids = set(span.get_tag(replay.EXCEPTION_ID_TAG) for span in self.spans)
            assert None not in exc_ids and len(exc_ids) == number_of_exc_ids

            # invoke again (should be in less than 1 sec)
            with with_rate_limiter(rate_limiter):
                with pytest.raises(KeyError):
                    c()

            self.assert_span_count(6)
            # no new snapshots
            assert len(uploader.collector.queue) == 3

    def test_debugger_capture_exception(self):
        def a(v):
            with self.trace("a") as span:
                try:
                    raise ValueError("hello", v)
                except Exception:
                    span.set_exc_info(*sys.exc_info())
                    # Check that we don't capture multiple times
                    span.set_exc_info(*sys.exc_info())

        def b():
            with self.trace("b"):
                a(42)

        with exception_replay() as uploader:
            with with_rate_limiter(RateLimiter(limit_rate=1, raise_on_exceed=False)):
                b()

            self.assert_span_count(2)
            assert len(uploader.collector.queue) == 1

            span_b, span_a = self.spans

            assert span_a.name == "a"
            assert span_a.get_tag(replay.DEBUG_INFO_TAG) == "true"
            assert span_b.get_tag(replay.DEBUG_INFO_TAG) is None

    def test_debugger_exception_ident_limit(self):
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
                limit_rate=float("inf"),  # no rate limit
                raise_on_exceed=False,
            )
            with with_rate_limiter(rate_limiter):
                with pytest.raises(KeyError):
                    c()

            self.assert_span_count(3)
            assert len(uploader.collector.queue) == 3

            # Invoke again. The same exception won't be captured again in quick
            # succession
            with with_rate_limiter(rate_limiter):
                with pytest.raises(KeyError):
                    c()

            self.assert_span_count(6)
            # no new snapshots
            assert len(uploader.collector.queue) == 3

    def test_debugger_exception_in_closure(self):
        def b():
            with self.trace("b"):
                nonloc = 4

                def a(v):
                    if nonloc:
                        raise ValueError("hello", v)

                a(nonloc)

        with exception_replay() as uploader:
            with with_rate_limiter(RateLimiter(limit_rate=1, raise_on_exceed=False)):
                with pytest.raises(ValueError):
                    b()

            assert all(
                s.line_capture["locals"]["nonloc"] == {"type": "int", "value": "4"} for s in uploader.collector.queue
            )

    def test_debugger_max_frames(self):
        config = ExceptionReplayConfig()
        root = None

        def r(n=config.max_frames * 2, c=None):
            if n == 0:
                if c is None:
                    raise ValueError("hello")
                else:
                    c()
            r(n - 1, c)

        def a():
            with self.trace("a"):
                r()

        def b():
            with self.trace("b"):
                r(10, a)

        def c():
            nonlocal root

            with self.trace("c") as root:
                r(10, b)

        with exception_replay() as uploader:
            rate_limiter = RateLimiter(
                limit_rate=float("inf"),  # no rate limit
                raise_on_exceed=False,
            )
            with with_rate_limiter(rate_limiter):
                with pytest.raises(ValueError):
                    c()

            self.assert_span_count(3)
            n = uploader.collector.queue
            assert len(n) == config.max_frames

            assert root.get_metric(replay.SNAPSHOT_COUNT_TAG) == config.max_frames

    def test_debugger_exception_replay_single_non_user_snapshot(self):
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

        with exception_replay() as uploader, override_third_party_packages(["tests"]):
            # We pretend that tests is a third-party package so that we can
            # skip all but one frames
            with with_rate_limiter(RateLimiter(limit_rate=1, raise_on_exceed=False)):
                with pytest.raises(ValueError):
                    c()

            self.assert_span_count(3)
            n_snapshots = len(uploader.collector.queue)
            assert n_snapshots == 1

            snapshots = {str(s.uuid): s for s in uploader.collector.queue}

            span = self.spans[-1]
            assert span.get_tag(replay.DEBUG_INFO_TAG) == "true"

            # Check that we have all the tags for each snapshot
            assert span.get_tag("_dd.debug.error.1.snapshot_id") in snapshots
            assert span.get_tag("_dd.debug.error.1.file") == __file__.replace(".pyc", ".py")
            assert span.get_tag("_dd.debug.error.1.function") == "a"
            assert span.get_tag("_dd.debug.error.1.line")
