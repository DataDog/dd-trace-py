"""Tests for path sanitization functions in Flask integration."""

import os
import pathlib
from unittest import mock

import pytest
from werkzeug.security import safe_join
from werkzeug.utils import secure_filename

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.secure_marks.sanitizers import path_traversal_sanitizer
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.utils import override_global_config


_ = _iast_patched_module("werkzeug.utils")
mod = _iast_patched_module("tests.appsec.integrations.fixtures.patch_file_paths")


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()


def test_werkzeug_secure_filename():
    file_path = "../../../etc/passwd; DROP TABLE users.txt"
    tainted = taint_pyobject(
        pyobject=file_path,
        source_name="test_sanitize_pymysql_converters",
        source_value=file_path,
        source_origin=OriginType.PARAMETER,
    )

    value = mod.werkzeug_secure_filename(tainted)
    ranges = get_tainted_ranges(value)
    assert value == "a-etc_passwd_DROP_TABLE_users.txt"
    assert len(ranges) > 0
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)
    assert is_pyobject_tainted(value)


def test_secure_filename_sanitizer():
    """Test that secure_filename correctly sanitizes and marks filenames as safe from path traversal."""
    # Create a tainted filename with path traversal attempt
    filename = "../../etc/passwd"
    tainted = taint_pyobject(
        pyobject=filename,
        source_name="test_secure_filename_sanitizer",
        source_value=filename,
        source_origin=OriginType.PARAMETER,
    )

    # Mock the secure_filename function
    secure_filename_mock = mock.Mock(return_value="passwd")

    # Apply the sanitizer
    result = path_traversal_sanitizer(secure_filename_mock, None, [tainted], {})

    # Verify the result is marked as secure
    ranges = get_ranges(result)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)


def test_secure_filename_real():
    """Test that werkzeug.utils.secure_filename actually gets wrapped and marks values as safe."""
    # Create a tainted filename
    filename = "../../../etc/passwd"
    tainted = taint_pyobject(
        pyobject=filename,
        source_name="test_secure_filename_real",
        source_value=filename,
        source_origin=OriginType.PARAMETER,
    )

    result = secure_filename(tainted)
    assert result == "passwd"  # secure_filename removes path components

    # Verify the result is marked as secure
    ranges = get_ranges(result)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)


def test_path_sanitizers_chain():
    """Test that multiple path sanitizers can be chained together safely."""
    # Create a tainted path with multiple traversal attempts
    path = "../../../../etc/passwd/./../../sensitive"
    tainted = taint_pyobject(
        pyobject=path,
        source_name="test_path_sanitizers_chain",
        source_value=path,
        source_origin=OriginType.PARAMETER,
    )

    # Apply multiple sanitizers in chain
    normalized = os.path.normpath(tainted)
    resolved = pathlib.Path(normalized).resolve()
    result = str(resolved)

    # Verify each step maintains or adds security marks
    for path_obj in [normalized, str(resolved), result]:
        ranges = get_ranges(path_obj)
        assert ranges
        for _range in ranges:
            assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)


def test_safe_join_sanitizer():
    """Test that werkzeug.security.safe_join correctly handles path joining and maintains security marks."""
    # Create a tainted path component
    path = "../../../etc/passwd"
    tainted = taint_pyobject(
        pyobject=path,
        source_name="test_safe_join_sanitizer",
        source_value=path,
        source_origin=OriginType.PARAMETER,
    )

    try:
        # safe_join will raise error for path traversal attempts
        result = safe_join("/safe/base/path", tainted)
        assert False, "safe_join should raise error for path traversal attempts"
    except ValueError:
        # This is the expected behavior
        pass

    # Test with a safe path
    safe_path = "safe_file.txt"
    tainted_safe = taint_pyobject(
        pyobject=safe_path,
        source_name="test_safe_join_sanitizer",
        source_value=safe_path,
        source_origin=OriginType.PARAMETER,
    )

    result = safe_join("/safe/base/path", tainted_safe)

    # Verify the result maintains security marks
    ranges = get_ranges(result)
    assert ranges
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.PATH_TRAVERSAL)
