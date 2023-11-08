"""
Common tests to aspects, like ensuring that they don't break when receiving extra arguments.
"""
import pytest

from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from tests.appsec.iast.aspects.conftest import _iast_patched_module


patched_callers = _iast_patched_module("tests.appsec.iast.fixtures.aspects.callers")


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.parametrize(
    "aspect, arg_a, arg_b, kwargs, expected_result",
    [
        ("bytearray_extend", bytearray("Foo", "utf-8"), b"Bar", {}, bytearray("FooBar", "utf-8")),
        ("bytearray_extend_with_kwargs", bytearray("Foo", "utf-8"), b"Bar", {}, bytearray("FooBar", "utf-8")),
        (
            "bytearray_extend_with_kwargs",
            bytearray("Foo", "utf-8"),
            b"Bar",
            {"dry_run": False},
            bytearray("FooBar", "utf-8"),
        ),
        (
            "bytearray_extend_with_kwargs",
            bytearray("Foo", "utf-8"),
            b"Bar",
            {"dry_run": True},
            bytearray("Foo", "utf-8"),
        ),
        (
            "bytearray_extend_with_kwargs_imported_directly",
            bytearray("Foo", "utf-8"),
            b"Bar",
            {"dry_run": False},
            bytearray("FooBar", "utf-8"),
        ),
        (
            "bytearray_extend_with_kwargs_imported_directly",
            bytearray("Foo", "utf-8"),
            b"Bar",
            {"dry_run": True},
            bytearray("Foo", "utf-8"),
        ),
    ],
)
def test_aspect_patched_result(aspect, arg_a, arg_b, kwargs, expected_result):
    assert getattr(patched_callers, aspect)(arg_a, arg_b, **kwargs) == expected_result
