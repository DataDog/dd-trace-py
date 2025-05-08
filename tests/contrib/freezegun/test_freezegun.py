import datetime
import os
import time

import pytest

from ddtrace.internal.utils.time import StopWatch
from ddtrace.trace import tracer as dd_tracer
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


class TestFreezegunTestCase:
    @pytest.fixture(autouse=True)
    def _patch_freezegun(self):
        from ddtrace.contrib.internal.freezegun.patch import patch
        from ddtrace.contrib.internal.freezegun.patch import unpatch

        patch()
        yield
        unpatch()

    def test_freezegun_unpatch(self):
        import freezegun

        from ddtrace.contrib.internal.freezegun.patch import unpatch

        unpatch()

        with freezegun.freeze_time("2020-01-01"):
            with dd_tracer.trace("freezegun.test") as span:
                time.sleep(1)

        assert span.duration == 0

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

        freezegun.configure(default_ignore_list=[])

        with freezegun.freeze_time("2020-01-01"):
            with dd_tracer.trace("freezegun.test") as span:
                time.sleep(1)

        assert span.duration >= 1


class PytestFreezegunTestCase(PytestTestCaseBase):
    def test_freezegun_pytest_plugin(self):
        """Tests that pytest's patching of freezegun in the v1 plugin version works"""
        import sys

        from ddtrace.contrib.internal.freezegun.patch import unpatch

        unpatch()
        if "freezegun" in sys.modules:
            del sys.modules["freezegun"]

        py_file = self.testdir.makepyfile(
            """
            import datetime
            import time

            import freezegun

            from ddtrace.trace import tracer as dd_tracer

            def test_pytest_patched_freezegun():
                with freezegun.freeze_time("2020-01-01"):
                    with dd_tracer.trace("freezegun.test") as span:
                        time.sleep(1)
                assert span.duration >= 1

        """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("--ddtrace", "-s", file_name)
        spans = self.pop_spans()

        assert len(spans) == 4
        for span in spans:
            assert span.get_tag("test.status") == "pass"
