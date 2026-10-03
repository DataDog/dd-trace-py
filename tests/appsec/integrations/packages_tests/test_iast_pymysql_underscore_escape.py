"""Regression tests for IAST wrapping of PyMySQL 1.2 ``Connection._escape_string``."""

import importlib
from types import ModuleType
from typing import Any

from pymysql.connections import Connection  # type: ignore[import-untyped]
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import VulnerabilityType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted


def test_sanitize_pymysql_underscore_escape_string_without_public_alias() -> None:
    """PyMySQL 1.2.1: ``escape_string`` is gone; sanitizer mark must still apply via ``_escape_string``."""
    if not hasattr(Connection, "_escape_string"):
        pytest.skip("PyMySQL <1.2 uses Connection.escape_string, not _escape_string")

    sanitizers: ModuleType = importlib.import_module("tests.appsec.integrations.packages_tests.test_iast_sanitizers")
    patch_modules: Any = sanitizers.patch_modules
    mod: ModuleType = patch_modules()
    sql: str = "'; DROP TABLE users; --"
    tainted: object = taint_pyobject(
        pyobject=sql,
        source_name="test_sanitize_pymysql_underscore_escape_string",
        source_value=sql,
        source_origin=OriginType.PARAMETER,
    )

    value: str = mod.pymysql_underscore_escape_string_without_public_alias(tainted)
    ranges: Any = get_tainted_ranges(value)
    assert value == "a-''; DROP TABLE users; --"
    assert len(ranges) > 0
    for _range in ranges:
        assert _range.has_secure_mark(VulnerabilityType.SQL_INJECTION)
    assert is_pyobject_tainted(value)
