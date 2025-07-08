import pytest

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.taint_sinks.header_injection import _iast_report_header_injection
from tests.appsec.iast.taint_sinks._taint_sinks_utils import NON_TEXT_TYPES_TEST_DATA


@pytest.mark.parametrize("non_text_obj,obj_type", NON_TEXT_TYPES_TEST_DATA)
def test_header_injection_non_text_types_no_vulnerability(non_text_obj, obj_type, iast_context_defaults):
    """Test that non-text types don't trigger header injection vulnerabilities."""
    # Taint the non-text object
    tainted_obj = taint_pyobject(
        non_text_obj,
        source_name="test_source",
        source_value=str(non_text_obj),
        source_origin=OriginType.PARAMETER,
    )

    # Call the header injection reporting function with non-text header value
    _iast_report_header_injection(("X-Test-Header", tainted_obj))

    # Assert no vulnerability was reported
    span_report = get_iast_reporter()
    assert span_report is None, f"Vulnerability reported for {obj_type}: {non_text_obj}"


@pytest.mark.parametrize("non_text_obj,obj_type", NON_TEXT_TYPES_TEST_DATA)
def test_header_injection_non_text_header_name_no_vulnerability(non_text_obj, obj_type, iast_context_defaults):
    """Test that non-text header names don't trigger vulnerabilities."""
    # Taint the non-text object for header name
    tainted_obj = taint_pyobject(
        non_text_obj,
        source_name="test_source",
        source_value=str(non_text_obj),
        source_origin=OriginType.PARAMETER,
    )

    # Call the header injection reporting function with non-text header name
    _iast_report_header_injection((tainted_obj, "test_value"))

    # Assert no vulnerability was reported
    span_report = get_iast_reporter()
    assert span_report is None, f"Vulnerability reported for {obj_type} header name: {non_text_obj}"
