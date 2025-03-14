import logging
import os
import sys
from unittest import mock

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking._context import create_context
from ddtrace.appsec._iast._taint_tracking._context import reset_context
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.utils import override_global_config


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.module_functions")


def test_ospathjoin_tainted():
    string_input = taint_pyobject(
        pyobject="foo", source_name="first_element", source_value="foo", source_origin=OriginType.PARAMETER
    )
    result = mod.do_os_path_join(string_input, "bar")
    assert result == "foo/bar"
    assert get_tainted_ranges(result) == [TaintRange(0, 3, Source("first_element", "foo", OriginType.PARAMETER))]


def test_ospathnormcase_tainted():
    string_input = taint_pyobject(
        pyobject="/foo/bar", source_name="first_element", source_value="/foo/bar", source_origin=OriginType.PARAMETER
    )
    result = mod.do_os_path_normcase(string_input)
    assert result == "/foo/bar"
    assert get_tainted_ranges(result) == [TaintRange(0, 8, Source("first_element", "/foo/bar", OriginType.PARAMETER))]


def test_ospathbasename_tainted():
    string_input = taint_pyobject(
        pyobject="/foo/bar", source_name="first_element", source_value="/foo/bar", source_origin=OriginType.PARAMETER
    )
    result = mod.do_os_path_basename(string_input)
    assert result == "bar"
    assert get_tainted_ranges(result) == [TaintRange(0, 3, Source("first_element", "/foo/bar", OriginType.PARAMETER))]


def test_ospathdirname_tainted():
    string_input = taint_pyobject(
        pyobject="/foo/bar", source_name="first_element", source_value="/foo/bar", source_origin=OriginType.PARAMETER
    )
    result = mod.do_os_path_dirname(string_input)
    assert result == "/foo"
    assert get_tainted_ranges(result) == [TaintRange(0, 4, Source("first_element", "/foo/bar", OriginType.PARAMETER))]


def test_ospathsplit_tainted():
    string_input = taint_pyobject(
        pyobject="/foo/bar", source_name="first_element", source_value="/foo/bar", source_origin=OriginType.PARAMETER
    )
    result = mod.do_os_path_split(string_input)
    assert result == ("/foo", "bar")
    assert get_tainted_ranges(result[0]) == [
        TaintRange(0, 4, Source("first_element", "/foo/bar", OriginType.PARAMETER))
    ]
    assert get_tainted_ranges(result[1]) == [
        TaintRange(0, 3, Source("first_element", "/foo/bar", OriginType.PARAMETER))
    ]


def test_ospathsplit_noaspect_dont_call_string_aspect():
    global mod

    with mock.patch("ddtrace.appsec._iast._taint_tracking.aspects.split_aspect") as str_split_aspect:
        with mock.patch("ddtrace.appsec._iast._taint_tracking.aspects._aspect_ospathsplit") as os_split_aspect:
            import ddtrace.appsec._iast._ast.visitor as visitor

            old_aspect = visitor._ASPECTS_SPEC["module_functions"]["os.path"]["split"]
            try:
                del visitor._ASPECTS_SPEC["module_functions"]["os.path"]["split"]
                del mod
                del sys.modules["benchmarks.bm.iast_fixtures.module_functions"]
                mod = _iast_patched_module("benchmarks.bm.iast_fixtures.module_functions")
                string_input = taint_pyobject(
                    pyobject="/foo/bar",
                    source_name="first_element",
                    source_value="/foo/bar",
                    source_origin=OriginType.PARAMETER,
                )
                result = mod.do_os_path_split(string_input)
                assert result == ("/foo", "bar")
                assert get_tainted_ranges(result[0]) == []
                assert get_tainted_ranges(result[1]) == []
                assert not str_split_aspect.called
                assert not os_split_aspect.called
            finally:
                visitor._ASPECTS_SPEC["module_functions"]["os.path"]["split"] = old_aspect
                del sys.modules["benchmarks.bm.iast_fixtures.module_functions"]
                mod = _iast_patched_module("benchmarks.bm.iast_fixtures.module_functions")


def test_ospathsplitext_tainted():
    string_input = taint_pyobject(
        pyobject="/foo/bar.txt",
        source_name="first_element",
        source_value="/foo/bar.txt",
        source_origin=OriginType.PARAMETER,
    )
    result = mod.do_os_path_splitext(string_input)
    assert result == ("/foo/bar", ".txt")
    assert get_tainted_ranges(result[0]) == [
        TaintRange(0, 8, Source("first_element", "/foo/bar.txt", OriginType.PARAMETER))
    ]
    assert get_tainted_ranges(result[1]) == [
        TaintRange(0, 4, Source("first_element", "/foo/bar.txt", OriginType.PARAMETER))
    ]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="requires python3.12 or higher")
def test_ospathsplitroot_tainted():
    string_input = taint_pyobject(
        pyobject="/foo/bar", source_name="first_element", source_value="/foo/bar", source_origin=OriginType.PARAMETER
    )
    result = mod.do_os_path_splitroot(string_input)
    assert result == ("", "/", "foo/bar")
    assert not get_tainted_ranges(result[0])
    assert get_tainted_ranges(result[1]) == [
        TaintRange(0, 1, Source("first_element", "/foo/bar", OriginType.PARAMETER))
    ]
    assert get_tainted_ranges(result[2]) == [
        TaintRange(0, 7, Source("first_element", "/foo/bar", OriginType.PARAMETER))
    ]


@pytest.mark.skipif(sys.version_info < (3, 12) and os.name != "nt", reason="Required Python 3.12 or Windows")
def test_ospathsplitdrive_tainted():
    string_input = taint_pyobject(
        pyobject="/foo/bar", source_name="first_element", source_value="/foo/bar", source_origin=OriginType.PARAMETER
    )
    result = mod.do_os_path_splitdrive(string_input)
    assert result == ("", "/foo/bar")
    assert not get_tainted_ranges(result[0])
    assert get_tainted_ranges(result[1]) == [
        TaintRange(0, 8, Source("first_element", "/foo/bar", OriginType.PARAMETER))
    ]


@pytest.mark.skip_iast_check_logs
@pytest.mark.skipif(sys.version_info < (3, 9, 0), reason="Python version not supported by IAST")
def test_propagate_ranges_with_no_context(caplog):
    """Test taint_pyobject without context. This test is to ensure that the function does not raise an exception."""
    input_str = "abcde"
    create_context()
    string_input = taint_pyobject(
        pyobject=input_str,
        source_name="test_add_aspect_tainting_left_hand",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    assert get_tainted_ranges(string_input)

    reset_context()
    with override_global_config(dict(_iast_debug=True)), caplog.at_level(logging.DEBUG):
        result = mod.do_os_path_join(string_input, "bar")
        assert result == "abcde/bar"
    log_messages = [record.message for record in caplog.get_records("call")]
    assert not any("iast::" in message for message in log_messages), log_messages


# TODO: add tests for os.path.splitdrive and os.path.normcase under Windows
