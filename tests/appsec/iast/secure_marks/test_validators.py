"""Tests for IAST secure marks validators."""

from unittest import mock

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.secure_marks.validators import cmdi_validator
from ddtrace.appsec._iast.secure_marks.validators import path_traversal_validator
from ddtrace.appsec._iast.secure_marks.validators import sqli_validator


def test_path_traversal_validator():
    """Test that path_traversal_validator marks filenames as safe from path traversal."""
    # Create a tainted filename
    filename = "../../etc/passwd"
    tainted = taint_pyobject(
        pyobject=filename,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=filename,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the validator function
    validator = mock.Mock(return_value=True)

    # Apply the validator
    path_traversal_validator(validator, None, [tainted], {})

    # Verify the argument is marked as secure
    ranges = get_ranges(tainted)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)


def test_sql_quote_validator():
    """Test that sqli_validator marks values as safe from SQL injection."""
    # Create a tainted SQL value
    sql = "'; DROP TABLE users; --"
    tainted = taint_pyobject(
        pyobject=sql,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=sql,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the validator function
    validator = mock.Mock(return_value=True)

    # Apply the validator
    sqli_validator(validator, None, [tainted], {})

    # Verify the argument is marked as secure
    ranges = get_ranges(tainted)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.SQL_INJECTION)


def test_command_quote_validator():
    """Test that cmdi_validator marks values as safe from command injection."""
    # Create a tainted command
    cmd = "; rm -rf /"
    tainted = taint_pyobject(
        pyobject=cmd,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=cmd,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the validator function
    validator = mock.Mock(return_value=True)

    # Apply the validator
    cmdi_validator(validator, None, [tainted], {})

    # Verify the argument is marked as secure
    ranges = get_ranges(tainted)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.COMMAND_INJECTION)
