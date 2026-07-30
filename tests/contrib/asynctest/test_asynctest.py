import sys

import pytest


# AIDEV-NOTE: This plugin installs mocks at import time so they are active before the
# child process initializes the Datadog pytest plugin. Keep this test out of inline_run:
# nested in-process pytest-cov sessions can corrupt the outer session's coverage state.
_INFRA_PLUGIN = """\
import pytest
from unittest.mock import patch

from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


_api_client_patch = patch(
    "ddtrace.testing.internal.session_manager.APIClient",
    return_value=mock_api_client_settings(),
)
_api_client_patch.start()
_standard_mocks = setup_standard_mocks()
_standard_mocks.__enter__()
_event_capture_context = EventCapture.capture()
_event_capture = _event_capture_context.__enter__()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session):
    events = list(_event_capture.events())
    assert sorted(event["type"] for event in events) == [
        "test",
        "test_module_end",
        "test_session_end",
        "test_suite_end",
    ]

    test_event = _event_capture.event_by_test_name("test_asynctest")
    assert test_event["content"]["meta"]["test.status"] == "pass"
"""


class TestPytest:
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch):
        self.testdir = testdir
        self.monkeypatch = monkeypatch

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
            test_asynctest="""
        import asynctest
        asynctest.CoroutineMock()

        def test_asynctest():
            assert 1 == 1
        """
        )
        self.testdir.makepyfile(asynctest_infra=_INFRA_PLUGIN)
        file_name = py_file.strpath
        self.monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", "0")

        rec = self.testdir.runpytest_subprocess("--ddtrace", "-p", "asynctest_infra", file_name)

        rec.assert_outcomes(passed=1)
