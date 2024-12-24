import os

import pytest

from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_schematized_service_name(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_service_name = {None: "falcon", "v0": "falcon", "v1": DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME}[
        schema_version
    ]
    code = """
import pytest
import falcon
import sys

from falcon import testing
from ddtrace.contrib.internal.falcon.patch import FALCON_VERSION
from tests.contrib.falcon.test_suite import FalconTestMixin
from tests.utils import TracerTestCase

from tests.contrib.falcon.app import get_app


class TestCase(TracerTestCase, testing.TestCase, FalconTestMixin):
    def setUp(self):
        super(TestCase, self).setUp()
        self.api = get_app(tracer=self.tracer)
        if FALCON_VERSION >= (2, 0, 0):
            self.client = testing.TestClient(self.api)
        else:
            self.client = self

    def test(self, query_string="", trace_query_string=False):
        out = self.make_test_call("/200", expected_status_code=200, query_string=query_string)
        traces = self.tracer.pop_traces()
        span = traces[0][0]
        assert span.service == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_service_name
    )
    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, (out, err)


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_schematized_operation_name(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_operation_name = {None: "falcon.request", "v0": "falcon.request", "v1": "http.server.request"}[
        schema_version
    ]
    code = """
import pytest
import falcon
import sys

from falcon import testing
from ddtrace.contrib.internal.falcon.patch import FALCON_VERSION
from tests.contrib.falcon.test_suite import FalconTestMixin
from tests.utils import TracerTestCase

from tests.contrib.falcon.app import get_app


class TestCase(TracerTestCase, testing.TestCase, FalconTestMixin):
    def setUp(self):
        super(TestCase, self).setUp()
        self.api = get_app(tracer=self.tracer)
        if FALCON_VERSION >= (2, 0, 0):
            self.client = testing.TestClient(self.api)
        else:
            self.client = self

    def test(self, query_string="", trace_query_string=False):
        out = self.make_test_call("/200", expected_status_code=200, query_string=query_string)
        traces = self.tracer.pop_traces()
        span = traces[0][0]
        assert span.name == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_operation_name
    )
    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, (out, err)
