import sys

import pytest


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
            """
        import asynctest
        asynctest.CoroutineMock()

        def test_asynctest():
            assert 1 == 1
        """
        )
        file_name = py_file.strpath
        self.monkeypatch.setenv("DD_CIVISIBILITY_FLAKY_RETRY_ENABLED", "0")
        rec = self.testdir.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
