import datetime
import time

from ddtrace.internal.utils.time import StopWatch
from ddtrace.trace import tracer as dd_tracer


class TestFreezegunTestCase:
    def test_freezegun_does_not_freeze_tracing(self):
        import freezegun

        with freezegun.freeze_time("2020-01-01"):
            with dd_tracer.trace("freezegun.test") as span:
                time.sleep(1)

        assert span.duration >= 1

    def test_freezegun_fast_forward_does_not_affect_tracing(self):
        import freezegun

        with freezegun.freeze_time("2020-01-01") as frozen_time:
            with dd_tracer.trace("freezegun.test") as span:
                time.sleep(1)
                frozen_time.tick(delta=datetime.timedelta(days=10))
        assert 1 <= span.duration <= 5

    def test_freezegun_does_not_freeze_stopwatch(self):
        import freezegun

        with freezegun.freeze_time("2020-01-01"):
            with StopWatch() as sw:
                time.sleep(1)
            assert sw.elapsed() >= 1

    def test_freezegun_configure_default_ignore_list_continues_to_ignore_ddtrace(self):
        import freezegun
        from freezegun.config import DEFAULT_IGNORE_LIST

        try:
            freezegun.configure(default_ignore_list=[])

            with freezegun.freeze_time("2020-01-01"):
                with dd_tracer.trace("freezegun.test") as span:
                    time.sleep(1)

            assert span.duration >= 1
        finally:
            # Reset the ignore list to its default value after the test
            freezegun.configure(default_ignore_list=DEFAULT_IGNORE_LIST)
