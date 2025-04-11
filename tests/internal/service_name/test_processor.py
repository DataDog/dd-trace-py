import os

import pytest

from ddtrace.internal.schema.processor import BaseServiceProcessor
from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME


@pytest.fixture
def processor():
    return BaseServiceProcessor()


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
@pytest.mark.parametrize("global_service_name", [None, "mysvc"])
def test_base_service(ddtrace_run_python_code_in_subprocess, schema_version, global_service_name):
    expected_base_service_name = {
        None: global_service_name or DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME,
        "v0": global_service_name or DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME,
        "v1": global_service_name or DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME,
    }[schema_version]

    code = """
import pytest
import sys

from ddtrace import config
from ddtrace.constants import _BASE_SERVICE_KEY
from ddtrace.internal.schema.processor import BaseServiceProcessor
from ddtrace.trace import Span
from tests.internal.service_name.test_processor import processor

def test(processor):
    fake_trace =  [
        Span(
            "test_service_matches",
            service=config.service,
            resource="test_resource",
        ),
        Span(
            "test_service_not_set",
            resource="test_resource",
        ),
        Span(
            "test_service_is_not_equal_to_global",
            service="test_service",
            resource="test_resource",
        ),
        Span(
            "test_service_is_case_insensitive",
            service=(config.service or "").title(),
            resource="test_resource",
        )
    ]

    processor.process_trace(fake_trace)
    assert _BASE_SERVICE_KEY not in fake_trace[0].get_tags()
    assert _BASE_SERVICE_KEY not in fake_trace[1].get_tags(), config.service
    assert fake_trace[2].get_tag(_BASE_SERVICE_KEY) is not None
    assert fake_trace[2].get_tag(_BASE_SERVICE_KEY) == '{}'
    assert _BASE_SERVICE_KEY not in fake_trace[3].get_tags(), fake_trace[3].service + fake_trace[3].get_tags()

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_base_service_name
    )

    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    if global_service_name is not None:
        env["DD_SERVICE"] = global_service_name
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, (out, err)
