import time

import pytest

from ddtrace import tracer as dd_tracer
from ddtrace.internal.utils.time import StopWatch


class TestFreezegunTestCase:
    @pytest.fixture(autouse=True)
    def _patch_freezegun(self):
        from ddtrace.contrib.freezegun import patch
        from ddtrace.contrib.freezegun import unpatch

        patch()
        yield
        unpatch()

    def test_freezegun_does_not_freeze_tracing(self):
        import freezegun

        with freezegun.freeze_time("2020-01-01"):
            with dd_tracer.trace("freezegun.test") as span:
                time.sleep(1)

        assert span.duration >= 1

    def test_freezegun_does_not_freeze_stopwatch(self):
        import freezegun

        with freezegun.freeze_time("2020-01-01"):
            with StopWatch() as sw:
                time.sleep(1)
            assert sw.elapsed() >= 1

    def test_freezegun_configure_default_ignore_list_continues_to_ignore_ddtrace(self):
        import freezegun

        freezegun.configure(default_ignore_list=[])

        with freezegun.freeze_time("2020-01-01"):
            with dd_tracer.trace("freezegun.test") as span:
                time.sleep(1)

        assert span.duration >= 1
