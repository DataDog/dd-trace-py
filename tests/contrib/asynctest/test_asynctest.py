import os
import sys
from unittest import mock

import pytest

import ddtrace
from ddtrace.contrib.internal.pytest.plugin import is_enabled
from ddtrace.ext import test
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.ci_visibility.util import _patch_dummy_writer
from tests.utils import DummyCIVisibilityWriter
from tests.utils import TracerTestCase
from tests.utils import override_env


class TestPytest(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch):
        self.testdir = testdir
        self.monkeypatch = monkeypatch

    @pytest.fixture(autouse=True)
    def _dummy_check_enabled_features(self):
        """By default, assume that _check_enabled_features() returns an ITR-disabled response.

        Tests that need a different response should re-patch the CIVisibility object.
        """
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            yield

    def inline_run(self, *args):
        """Execute test script with test tracer."""

        class CIVisibilityPlugin:
            @staticmethod
            def pytest_configure(config):
                if is_enabled(config):
                    with _patch_dummy_writer():
                        assert CIVisibility.enabled
                        CIVisibility.disable()
                        CIVisibility.enable(tracer=self.tracer, config=ddtrace.config.pytest)

        with override_env(dict(DD_API_KEY="foobar.baz")):
            self.tracer._writer = DummyCIVisibilityWriter("https://citestcycle-intake.banana")
            self.tracer._recreate()
            return self.testdir.inline_run(*args, plugins=[CIVisibilityPlugin()])

    @pytest.mark.skipif(
        sys.version_info >= (3, 11, 0),
        reason="asynctest isn't working on Python 3.11, asynctest "
        "raisesAttributeError: module 'asyncio' has no "
        "attribute 'coroutine'",
    )
    def test_asynctest_not_raise_attribute_error_exception(self):
        """Test AttributeError exception in `ddtrace/vendor/wrapt/wrappers.py` when try to import asynctest package.
        Issue: https://github.com/DataDog/dd-trace-py/issues/4484
        """
        py_file = self.testdir.makepyfile(
            """
        import asynctest
        asynctest.CoroutineMock()

        def test_asynctest():
            assert 1 == 1
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 4
        test_span = spans[0]
        assert test_span.get_tag(test.STATUS) == test.Status.PASS.value
