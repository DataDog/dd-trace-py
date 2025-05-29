import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from tests.appsec.iast.iast_utils import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")

_SOURCE1 = Source("test", "foobar", OriginType.PARAMETER)


@pytest.mark.parametrize(
    "input_str, output_str, mod_function, check_ranges",
    [
        ("foobar", "FOOBAR", mod.do_upper, True),
        (b"foobar", b"FOOBAR", mod.do_upper, True),
        (bytearray(b"foobar"), bytearray(b"FOOBAR"), mod.do_upper, True),
        ("FooBar", "foobar", mod.do_lower, True),
        (b"FooBar", b"foobar", mod.do_lower, True),
        (bytearray(b"FooBar"), bytearray(b"foobar"), mod.do_lower, True),
        ("FooBar", "fOObAR", mod.do_swapcase, True),
        (b"FooBar", b"fOObAR", mod.do_swapcase, True),
        (bytearray(b"FooBar"), bytearray(b"fOObAR"), mod.do_swapcase, True),
        ("fo baz", "Fo Baz", mod.do_title, True),
        (b"fo baz", b"Fo Baz", mod.do_title, True),
        (bytearray(b"fo baz"), bytearray(b"Fo Baz"), mod.do_title, True),
        ("foobar", "Foobar", mod.do_capitalize, True),
        (b"foobar", b"Foobar", mod.do_capitalize, True),
        (bytearray(b"foobar"), bytearray(b"Foobar"), mod.do_capitalize, True),
        ("FooBar", "foobar", mod.do_casefold, True),
        # These check that if the object is not text, but an object with a method with
        # the same name, we're not replacing it
        ("foobar", "output", mod.do_upper_not_str, False),
        (b"foobar", "output", mod.do_upper_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_upper_not_str, False),
        ("foobar", "output", mod.do_lower_not_str, False),
        (b"foobar", "output", mod.do_lower_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_lower_not_str, False),
        ("foobar", "output", mod.do_swapcase_not_str, False),
        (b"foobar", "output", mod.do_swapcase_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_swapcase_not_str, False),
        ("foobar", "output", mod.do_title_not_str, False),
        (b"foobar", "output", mod.do_title_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_title_not_str, False),
        ("foobar", "output", mod.do_capitalize_not_str, False),
        (b"foobar", "output", mod.do_capitalize_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_capitalize_not_str, False),
        ("foobar", "output", mod.do_encode_not_str, False),
        (b"foobar", "output", mod.do_encode_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_encode_not_str, False),
        ("foobar", "output", mod.do_expandtabs_not_str, False),
        (b"foobar", "output", mod.do_expandtabs_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_expandtabs_not_str, False),
        ("foobar", "output", mod.do_casefold_not_str, False),
        (b"foobar", "output", mod.do_casefold_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_casefold_not_str, False),
        ("foobar", "output", mod.do_center_not_str, False),
        (b"foobar", "output", mod.do_center_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_center_not_str, False),
        ("foobar", "output", mod.do_ljust_not_str, False),
        (b"foobar", "output", mod.do_ljust_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_ljust_not_str, False),
        ("foobar", "output", mod.do_lstrip_not_str, False),
        (b"foobar", "output", mod.do_lstrip_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_lstrip_not_str, False),
        ("foobar", "output", mod.do_rstrip_not_str, False),
        (b"foobar", "output", mod.do_rstrip_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_rstrip_not_str, False),
        ("foobar", "output", mod.do_split_not_str, False),
        (b"foobar", "output", mod.do_split_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_split_not_str, False),
        ("foobar", "output", mod.do_rsplit_not_str, False),
        (b"foobar", "output", mod.do_rsplit_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_rsplit_not_str, False),
        ("foobar", "output", mod.do_splitlines_not_str, False),
        (b"foobar", "output", mod.do_splitlines_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_splitlines_not_str, False),
        ("foobar", "output", mod.do_partition_not_str, False),
        (b"foobar", "output", mod.do_partition_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_partition_not_str, False),
        ("foobar", "output", mod.do_rpartition_not_str, False),
        (b"foobar", "output", mod.do_rpartition_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_rpartition_not_str, False),
        ("foobar", "output", mod.do_replace_not_str, False),
        (b"foobar", "output", mod.do_replace_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_replace_not_str, False),
        ("foobar", "output", mod.do_format_not_str, False),
        (b"foobar", "output", mod.do_format_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_format_not_str, False),
        ("foobar", "output", mod.do_format_map_not_str, False),
        (b"foobar", "output", mod.do_format_map_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_format_map_not_str, False),
        ("foobar", "output", mod.do_zfill_not_str, False),
        (b"foobar", "output", mod.do_zfill_not_str, False),
        (bytearray(b"foobar"), "output", mod.do_zfill_not_str, False),
    ],
)
def test_common_replace_aspects(input_str, output_str, mod_function, check_ranges):
    assert not get_tainted_ranges(input_str)
    res = mod_function(input_str)
    assert res == output_str

    if not check_ranges:
        return

    assert not get_tainted_ranges(input_str)
    assert not get_tainted_ranges(res)

    s_tainted = taint_pyobject(
        pyobject=input_str, source_name=_SOURCE1.name, source_value=_SOURCE1.value, source_origin=_SOURCE1.origin
    )
    res = mod_function(s_tainted)
    assert res == output_str
    assert get_tainted_ranges(res) == [TaintRange(0, 6, _SOURCE1)]
    assert not get_tainted_ranges(input_str)


def test_translate():
    input_str = "foobar"
    translate_dict = str.maketrans({"f": "g", "r": "z"})
    output_str = "goobaz"

    assert not get_tainted_ranges(input_str)
    res = mod.do_translate(input_str, translate_dict)
    assert res == output_str
    assert not get_tainted_ranges(input_str)
    assert not get_tainted_ranges(res)

    s_tainted = taint_pyobject(
        pyobject=input_str, source_name=_SOURCE1.name, source_value=_SOURCE1.value, source_origin=_SOURCE1.origin
    )
    res = mod.do_translate(s_tainted, translate_dict)
    assert res == output_str
    assert get_tainted_ranges(res) == [TaintRange(0, 6, _SOURCE1)]
    assert not get_tainted_ranges(input_str)


def test_upper_in_decorator():
    s = "foobar"
    assert not get_tainted_ranges(s)

    s2 = "barbaz"
    s_tainted = taint_pyobject(
        pyobject=s2, source_name=_SOURCE1.name, source_value=_SOURCE1.value, source_origin=_SOURCE1.origin
    )

    res = mod.do_add_and_uppercase(s, s_tainted)
    assert res == "FOOBARBARBAZ"
    assert get_tainted_ranges(res) == [TaintRange(6, 6, _SOURCE1)]

    res2 = mod.do_add_and_uppercase(s_tainted, s)
    assert get_tainted_ranges(res2) == [TaintRange(0, 6, _SOURCE1)]
