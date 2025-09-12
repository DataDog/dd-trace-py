import pytest

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.taint_sinks.unvalidated_redirect import _iast_report_unvalidated_redirect
from tests.appsec.iast.taint_sinks._taint_sinks_utils import NON_TEXT_TYPES_TEST_DATA


@pytest.mark.parametrize("non_text_obj,obj_type", NON_TEXT_TYPES_TEST_DATA)
def test_unvalidated_redirect_non_text_types_no_vulnerability(non_text_obj, obj_type, iast_context_defaults):
    """Test that non-text types don't trigger unvalidated redirect vulnerabilities."""
    # Taint the non-text object
    tainted_obj = taint_pyobject(
        non_text_obj,
        source_name="test_source",
        source_value=str(non_text_obj),
        source_origin=OriginType.PARAMETER,
    )

    # Call the unvalidated redirect reporting function directly
    _iast_report_unvalidated_redirect(tainted_obj)

    # Assert no vulnerability was reported
    span_report = get_iast_reporter()
    assert span_report is None, f"Vulnerability reported for {obj_type}: {non_text_obj}"
