from collections import Counter
from contextlib import contextmanager
import sys
from unittest import mock

import pytest

import ddtrace
from ddtrace.debugging._exception import replay
from ddtrace.debugging._session import Session
from ddtrace.internal.packages import _third_party_packages
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.internal.settings.exception_replay import ExceptionReplayConfig
from tests.debugging.mocking import exception_replay
from tests.utils import TracerTestCase
from tests.utils import override_third_party_packages


@pytest.fixture(autouse=True)
def clear_exception_ident_limiter():
    replay.EXCEPTION_IDENT_LIMITER.clear()
    yield
    replay.EXCEPTION_IDENT_LIMITER.clear()


def test_tb_frames_from_exception_chain():
    def a(v, d=None):
        if not v:
            raise ValueError("hello", v)

    def b(bar):
        m = 4
        try:
            a(bar % m)
        except ValueError:
            raise KeyError("chain it")

    def c(foo=42):
        sh = 3
        b(foo << sh)

    try:
        c()
    except Exception as e:
        chain, _ = replay.unwind_exception_chain(e, e.__traceback__)
        frames = list(replay.get_tb_frames_from_exception_chain(chain))
        # There are two tracebacks in the chain: one for KeyError and one for
        # ValueError. The innermost goes from the call to a in b up to the point
        # where the exception is raised in a. The outermost goes from the call
        # in this test function up to the point in b where the exception from a
        # is caught and the the KeyError is raised.
        assert len(frames) == 2 + 3
        assert [(n, f.tb_frame.f_code.co_name) for n, f in frames] == [
            (2, "a"),
            (1, "b"),
            (5, "b"),
            (4, "c"),
            (3, "test_tb_frames_from_exception_chain"),
        ]


def test_exception_replay_config_enabled(monkeypatch):
    monkeypatch.setenv("DD_EXCEPTION_REPLAY_ENABLED", "1")

    er_config = ExceptionReplayConfig()
    assert er_config.enabled


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
                    assert span.get_tag("_dd.debug.error.%d.snapshot_id" % i) in snapshots, span._get_str_attributes()
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

            assert root is not None
            assert root.get_metric(replay.SNAPSHOT_COUNT_TAG) == config.max_frames

            # Get all the function names attached to the root span
            fs = {v for k, v in root.get_tags().items() if k.startswith("_dd.debug.error.") and k.endswith(".function")}

            # The recursion has saturated the max frames so we should have
            # only the function 'r' in the snapshots
            assert fs == {"r"}, fs

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


def test_replay_functions_benchmark(benchmark):
    """Benchmark replay.py functions directly without tracer overhead."""
    import uuid

    from ddtrace.trace import Span

    def create_chained_exception(depth):
        """Create a chain of exceptions with specified depth."""
        try:
            if depth == 0:
                raise ValueError(uuid.uuid4())
            else:
                create_chained_exception(depth - 1)
        except Exception:
            raise RuntimeError(f"level {depth}") from None

    def get_exception_with_traceback(depth):
        """Capture an exception with its traceback."""
        try:
            create_chained_exception(depth)
        except RuntimeError as e:
            return e, e.__traceback__
        return None, None

    # Pre-create exceptions to benchmark just the replay functions
    with exception_replay() as uploader:
        handler = replay.SpanExceptionHandler()
        handler.__uploader__ = uploader.collector
        exc, tb = get_exception_with_traceback(100)
        span = Span("test")

        def run_replay_functions():
            # This benchmarks just unwind_exception_chain and get_tb_frames_from_exception_chain
            chain, exc_id = replay.unwind_exception_chain(exc, tb)
            # Consume the generator fully to measure its cost
            frames = list(replay.get_tb_frames_from_exception_chain(chain))

            called = False
            if tb is not None and exc_id is not None:
                called = True
                for _, _tb in frames:
                    handler._attach_tb_frame_snapshot_to_span(span, _tb, exc_id, only_user_code=False)

            assert len(frames) > 0
            assert called

        benchmark(run_replay_functions)


def test_limit_exception_allows_after_hourglass_expires():
    """limit_exception returns False when a known exception's HG has stopped trickling."""
    exc_ident = 99999

    # First call: registers HG and returns False (new exception)
    assert replay.limit_exception(exc_ident) is False

    # Force the hourglass to stop trickling by setting _end_at to the past
    hg = replay.EXCEPTION_IDENT_LIMITER[exc_ident]
    hg._end_at = 0  # in the past → trickling() will return False

    # Second call: HG exists, not trickling → turns it and returns False
    assert replay.limit_exception(exc_ident) is False


def test_limit_exception_evicts_when_over_1024():
    """limit_exception evicts old entries when EXCEPTION_IDENT_LIMITER exceeds 1024."""
    # Flood with 1025 distinct idents to trigger the eviction path
    for i in range(1025):
        replay.limit_exception(i)

    # Eviction fires at >1024: deletes the 256 oldest, leaving at most 1025 - 256 = 769
    assert len(replay.EXCEPTION_IDENT_LIMITER) <= 1025 - 256


def test_get_tb_frames_skips_none_tb_in_chain():
    """get_tb_frames_from_exception_chain skips chain entries with tb=None."""
    try:
        raise ValueError("test")
    except ValueError as e:
        real_tb = e.__traceback__

    # Build a chain where one entry has tb=None
    chain = replay.ExceptionChain([(ValueError("no tb"), None), (ValueError("with tb"), real_tb)])
    frames = list(replay.get_tb_frames_from_exception_chain(chain))

    # Only the entry with a real tb should yield frames
    assert len(frames) > 0
    assert all(f is not None for _, f in frames)


def test_can_capture_returns_true_in_debug_session():
    """can_capture returns True without consuming rate limit when in a debug session."""
    mock_root = mock.Mock()
    mock_root.get_tag.return_value = None  # info_captured is None
    mock_span = mock.Mock()
    mock_span._local_root = mock_root

    with mock.patch.object(Session, "from_trace", return_value=[mock.Mock()]):
        assert replay.can_capture(mock_span)


def test_can_capture_raises_on_unexpected_tag_value():
    """can_capture raises ValueError when CAPTURE_TRACE_TAG has an unexpected value (lines 252-253)."""
    mock_root = mock.Mock()
    mock_root.get_tag.return_value = "unexpected"
    mock_span = mock.Mock()
    mock_span._local_root = mock_root

    with pytest.raises(ValueError, match="unexpected value"):
        replay.can_capture(mock_span)


def test_attach_snapshot_returns_false_when_no_collector():
    """_attach_tb_frame_snapshot_to_span returns False when no collector is available (lines 309-310)."""
    tb = None
    try:
        raise ValueError("test")
    except ValueError as e:
        tb = e.__traceback__

    import uuid

    handler = replay.SpanExceptionHandler()
    exc_id = uuid.uuid4()
    mock_span = mock.Mock()

    with mock.patch.object(replay.SignalUploader, "get_collector", return_value=None):
        assert not handler._attach_tb_frame_snapshot_to_span(mock_span, tb, exc_id, only_user_code=False)


def test_on_span_exception_skips_empty_chain():
    """on_span_exception returns early when the exception chain is empty (line 342)."""
    mock_span = mock.Mock()
    mock_span.get_tag.return_value = None

    handler = replay.SpanExceptionHandler()

    # Patch unwind_exception_chain to return an empty chain
    with mock.patch.object(replay, "unwind_exception_chain", return_value=(replay.ExceptionChain(), None)):
        with mock.patch.object(replay, "can_capture", return_value=True):
            handler.on_span_exception(mock_span, ValueError, ValueError("x"), None)

    mock_span._set_attribute.assert_not_called()


def test_span_exception_handler_enable_idempotent():
    """SpanExceptionHandler.enable is a no-op when already enabled (lines 388-389)."""
    with mock.patch.object(replay.SignalUploader, "register"), mock.patch("ddtrace.internal.core.on"):
        try:
            replay.SpanExceptionHandler.enable()
            first_instance = replay.SpanExceptionHandler._instance
            # Second call must be a no-op
            replay.SpanExceptionHandler.enable()
            assert replay.SpanExceptionHandler._instance is first_instance
        finally:
            with (
                mock.patch("ddtrace.internal.core.reset_listeners"),
                mock.patch.object(replay.SignalUploader, "unregister"),
            ):
                replay.SpanExceptionHandler.disable()


def test_span_exception_handler_disable_idempotent():
    """SpanExceptionHandler.disable is a no-op when not enabled (lines 403-404)."""
    assert replay.SpanExceptionHandler._instance is None
    # Should not raise
    replay.SpanExceptionHandler.disable()
    assert replay.SpanExceptionHandler._instance is None


def test_unwind_exception_chain_circular_reference():
    """Test that unwind_exception_chain handles circular exception chains."""
    import signal

    exc1 = ValueError("first")
    exc2 = RuntimeError("second")
    exc1.__cause__ = exc2
    exc2.__cause__ = exc1

    def timeout_handler(signum, frame):
        raise TimeoutError("unwind_exception_chain stuck in infinite loop")

    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(2)

    try:
        chain, exc_id = replay.unwind_exception_chain(exc1, exc1.__traceback__)
        assert len(chain) <= 2
    except TimeoutError:
        pytest.fail("unwind_exception_chain entered an infinite loop on circular __cause__ chain")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
