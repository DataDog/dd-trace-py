import os
import sys

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.module_functions")


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


# TODO: add tests for os.path.splitdrive and os.path.normcase under Windows
