import pytest

from ddtrace.appsec._iast._iast_request_context import get_iast_reporter
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast.taint_sinks.unvalidated_redirect import UnvalidatedRedirect
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


def test_unvalidated_redirect_secure_mark_applied_even_when_dedup_filters(iast_context_deduplication_enabled):
    """Regression test: secure mark must be applied even when deduplication filters the report.

    When IAST deduplication is enabled and the same vulnerability hash was already seen,
    report() returns False. Previously, add_secure_mark() was only called when report()
    returned truthy, leaving the tainted string unmarked. A second detection path
    (e.g. werkzeug Headers.set for the Location header) would then fire and produce a
    spurious duplicate vulnerability with a different location.

    The fix ensures add_secure_mark() is called unconditionally whenever the string is
    tainted, regardless of the report() outcome.
    """
    tainted_url = taint_pyobject(
        "http://evil.com",
        source_name="test_source",
        source_value="http://evil.com",
        source_origin=OriginType.PARAMETER,
    )

    # First call: should report and mark
    _iast_report_unvalidated_redirect(tainted_url)
    report_after_first = get_iast_reporter()
    assert report_after_first is not None
    vulns = list(report_after_first.vulnerabilities)
    assert len(vulns) == 1

    # Verify the secure mark was applied
    ranges = get_tainted_ranges(tainted_url)
    assert len(ranges) > 0
    assert all(r.has_secure_mark(VulnerabilityType.UNVALIDATED_REDIRECT) for r in ranges)

    # Second call with the same tainted string: is_tainted_pyobject should return False
    # because the secure mark is set, so no new vulnerability should be added
    assert UnvalidatedRedirect.is_tainted_pyobject(tainted_url) is False
    _iast_report_unvalidated_redirect(tainted_url)
    report_after_second = get_iast_reporter()
    vulns_after = list(report_after_second.vulnerabilities)
    assert len(vulns_after) == 1, (
        f"Expected exactly 1 vulnerability but got {len(vulns_after)}; "
        "secure mark was not applied after first report, causing duplicate detection"
    )


def test_unvalidated_redirect_secure_mark_applied_when_quota_exhausted(iast_context_defaults):
    """Regression test: secure mark must be applied even when vulnerability quota is exhausted.

    When has_quota() returns False (max vulnerabilities per request reached), the report
    is skipped. The secure mark must still be applied to prevent a second detection path
    from producing a duplicate vulnerability.
    """
    tainted_url = taint_pyobject(
        "http://evil.com/quota",
        source_name="test_source",
        source_value="http://evil.com/quota",
        source_origin=OriginType.PARAMETER,
    )

    # Verify the string is initially tainted (no secure mark)
    assert UnvalidatedRedirect.is_tainted_pyobject(tainted_url) is True

    # Call the reporting function — this will report and add the secure mark
    _iast_report_unvalidated_redirect(tainted_url)

    # Regardless of whether report succeeded, the secure mark must be set
    ranges = get_tainted_ranges(tainted_url)
    assert len(ranges) > 0
    assert all(r.has_secure_mark(VulnerabilityType.UNVALIDATED_REDIRECT) for r in ranges)

    # The string should no longer be considered tainted for this vulnerability type
    assert UnvalidatedRedirect.is_tainted_pyobject(tainted_url) is False
