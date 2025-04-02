"""Tests for IAST secure marks sanitizers."""


from unittest import mock

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.secure_marks.sanitizers import cmdi_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import path_traversal_sanitizer
from ddtrace.appsec._iast.secure_marks.sanitizers import sqli_sanitizer
from tests.appsec.iast.iast_utils import _iast_patched_module


_iast_patched_module("shlex")
mod = _iast_patched_module("tests.appsec.iast.fixtures.secure_marks.sanitizers")


def test_secure_filename_sanitizer():
    """Test that path_traversal_sanitizer marks filenames as safe from path traversal."""
    # Create a tainted filename
    filename = "../../etc/passwd"
    tainted = taint_pyobject(
        pyobject=filename,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=filename,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the secure_filename function
    secure_filename = mock.Mock(return_value=tainted)

    # Apply the sanitizer
    result = path_traversal_sanitizer(secure_filename, None, [tainted], {})

    assert result is tainted
    # Verify the result is marked as secure
    ranges = get_ranges(result)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)


def test_sql_quote_sanitizer():
    """Test that sqli_sanitizer marks values as safe from SQL injection."""
    # Create a tainted SQL value
    sql = "'; DROP TABLE users; --"
    tainted = taint_pyobject(
        pyobject=sql,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=sql,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the quote function
    quote = mock.Mock(return_value=tainted)

    # Apply the sanitizer
    result = sqli_sanitizer(quote, None, [tainted], {})

    # Verify the result is marked as secure
    ranges = get_ranges(result)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.SQL_INJECTION)


def test_command_quote_sanitizer():
    """Test that cmdi_sanitizer marks values as safe from command injection."""
    # Create a tainted command
    cmd = "; rm -rf /"
    tainted = taint_pyobject(
        pyobject=cmd,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=cmd,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the quote function
    quote = mock.Mock(return_value=tainted)

    # Apply the sanitizer
    result = cmdi_sanitizer(quote, None, [tainted], {})

    # Verify the result is marked as secure
    ranges = get_ranges(result)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.COMMAND_INJECTION)


def test_real_shlex_quote():
    """Test that shlex.quote actually gets wrapped and marks values as secure."""
    # Create a tainted command
    cmd = "; rm -rf /"
    tainted = taint_pyobject(
        pyobject=cmd,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=cmd,
        source_origin=OriginType.PARAMETER,
    )
    result = mod.shlex_quote(tainted)
    assert result == "'; rm -rf /'"
    # Verify the result is marked as secure
    ranges = get_ranges(result)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.COMMAND_INJECTION)


def test_real_shlex_quote_move_ranges():
    """Test that shlex.quote actually gets wrapped and marks values as secure."""
    # Create a tainted command
    cmd = "; rm -rf /"
    tainted = taint_pyobject(
        pyobject=cmd,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=cmd,
        source_origin=OriginType.PARAMETER,
    )
    result = mod.shlex_quote_move_ranges(tainted)
    assert result == "12345-no-tainted'; rm -rf /'"
    # Verify the result is marked as secure
    ranges = get_ranges(result)
    assert len(ranges) == 1
    assert ranges[0].start == 17
    assert ranges[0].has_secure_mark(VulnerabilityType.COMMAND_INJECTION)
