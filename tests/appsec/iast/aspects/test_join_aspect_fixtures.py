#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import pytest


try:
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject
    from tests.appsec.iast.aspects.conftest import _iast_patched_module
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)

mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")


class TestOperatorJoinReplacement(object):
    def test_string_join_tainted_joiner(self):  # type: () -> None
        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            pyobject="-joiner-", source_name="joiner", source_value="foo", source_origin=OriginType.PARAMETER
        )
        it = ["a", "b", "c"]

        result = mod.do_join(string_input, it)
        assert result == "a-joiner-b-joiner-c"
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "-joiner-"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "-joiner-"

    def test_string_join_tainted_joiner_bytes(self):  # type: () -> None
        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            pyobject=b"-joiner-", source_name="joiner", source_value="foo", source_origin=OriginType.PARAMETER
        )
        it = [b"a", b"b", b"c"]
        result = mod.do_join(string_input, it)
        assert result == b"a-joiner-b-joiner-c"
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == b"-joiner-"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == b"-joiner-"

    def test_string_join_tainted_joiner_bytes_bytearray(self):  # type: () -> None
        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            pyobject=b"-joiner-", source_name="joiner", source_value="foo", source_origin=OriginType.PARAMETER
        )
        it = [bytearray(b"a"), bytearray(b"b"), bytearray(b"c")]
        result = mod.do_join(string_input, it)
        assert result == b"a-joiner-b-joiner-c"
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == b"-joiner-"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == b"-joiner-"

    def test_string_join_tainted_joiner_bytearray(self):  # type: () -> None
        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            pyobject=bytearray(b"-joiner-"),
            source_name="joiner",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        it = [bytearray(b"a"), bytearray(b"b"), bytearray(b"c")]

        result = mod.do_join(string_input, it)
        assert result == bytearray(b"a-joiner-b-joiner-c")
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == bytearray(b"-joiner-")
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == bytearray(b"-joiner-")

    def test_string_join_tainted_joiner_bytearray_bytes(self):  # type: () -> None
        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            pyobject=bytearray(b"-joiner-"),
            source_name="joiner",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        it = [b"a", b"b", b"c"]

        result = mod.do_join(string_input, it)
        assert result == bytearray(b"a-joiner-b-joiner-c")
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == bytearray(b"-joiner-")
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == bytearray(b"-joiner-")

    def test_string_join_tainted_joined(self):  # type: () -> None
        string_input = "-joiner-"
        it = [
            taint_pyobject(
                pyobject="aaaa", source_name="joiner", source_value="foo", source_origin=OriginType.PARAMETER
            ),
            "bbbb",
            taint_pyobject(
                pyobject="cccc", source_name="joiner", source_value="foo", source_origin=OriginType.PARAMETER
            ),
        ]

        result = mod.do_join(string_input, it)
        ranges = get_tainted_ranges(result)
        assert result == "aaaa-joiner-bbbb-joiner-cccc"
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "aaaa"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "cccc"

    def test_string_join_tainted_all(self):  # type: () -> None
        string_input = taint_pyobject(
            pyobject="-joiner-", source_name="joiner", source_value="foo", source_origin=OriginType.PARAMETER
        )
        it = [
            taint_pyobject(
                pyobject="aaaa",
                source_name="joiner",
                source_value="foo",
                source_origin=OriginType.PARAMETER,
            ),
            "bbbb",
            taint_pyobject(
                pyobject="cccc",
                source_name="joiner",
                source_value="foo",
                source_origin=OriginType.PARAMETER,
            ),
            taint_pyobject(
                pyobject="dddd",
                source_name="joiner",
                source_value="foo",
                source_origin=OriginType.PARAMETER,
            ),
            taint_pyobject(
                pyobject="eeee",
                source_name="joiner",
                source_value="foo",
                source_origin=OriginType.PARAMETER,
            ),
            taint_pyobject(
                pyobject="ffff",
                source_name="joiner",
                source_value="foo",
                source_origin=OriginType.PARAMETER,
            ),
            taint_pyobject(
                pyobject="gggg",
                source_name="joiner",
                source_value="foo",
                source_origin=OriginType.PARAMETER,
            ),
        ]

        result = mod.do_join(string_input, it)
        ranges = get_tainted_ranges(result)
        assert result == "aaaa-joiner-bbbb-joiner-cccc-joiner-dddd-joiner-eeee-joiner-ffff-joiner-gggg"
        pos = 0
        for results in (
            "aaaa",
            "-joiner-",
            "-joiner-",
            "cccc",
            "-joiner-",
            "dddd",
            "-joiner-",
            "eeee",
            "-joiner-",
            "ffff",
            "-joiner-",
            "gggg",
        ):
            assert result[ranges[pos].start : (ranges[pos].start + ranges[pos].length)] == results
            pos += 1

    def test_string_join_tuple(self):  # type: () -> None
        # Not tainted
        base_string = "abcde"
        result = mod.do_join_tuple(base_string)
        assert result == "abcde1abcde2abcde3"
        assert not get_tainted_ranges(result)

        # Tainted
        tainted_base_string = taint_pyobject(
            pyobject="abcde",
            source_name="joiner",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_tuple(tainted_base_string)
        assert result == "abcde1abcde2abcde3"

        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "abcde"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "abcde"
        assert result[ranges[2].start : (ranges[2].start + ranges[2].length)] == "abcde"

    def test_string_join_set(self):  # type: () -> None
        # Not tainted
        base_string = "abcde"
        result = mod.do_join_set(base_string)
        assert not get_tainted_ranges(result)

        # Tainted
        tainted_base_string = taint_pyobject(
            pyobject="abcde",
            source_name="joiner",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_set(tainted_base_string)

        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "abcde"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "abcde"
        assert result[ranges[2].start : (ranges[2].start + ranges[2].length)] == "abcde"

    def test_string_join_generator(self):
        # type: () -> None
        # Not tainted
        base_string = "abcde"
        result = mod.do_join_generator(base_string)
        assert result == "abcdeabcdeabcde"
        assert not get_tainted_ranges(result)

        # Tainted
        tainted_base_string = taint_pyobject(
            pyobject="abcde",
            source_name="joiner",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_generator(tainted_base_string)
        assert result == "abcdeabcdeabcde"

        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "abcde"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "abcde"
        assert result[ranges[2].start : (ranges[2].start + ranges[2].length)] == "abcde"

    def test_string_join_args_kwargs(self):
        # type: () -> None
        # Not tainted
        base_string = "-abcde-"
        result = mod.do_join_args_kwargs(base_string, ("f", "g"))
        assert result == "f-abcde-g"

        # Tainted
        tainted_base_string = taint_pyobject(
            pyobject="-abcde-",
            source_name="joiner",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_args_kwargs(tainted_base_string, ("f", "g"))
        assert result == "f-abcde-g"

        ranges = get_tainted_ranges(result)
        assert len(ranges) == 1
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "-abcde-"

    def test_string_join_empty_iterable_joiner_tainted(self):
        # type: () -> None
        # Not tainted
        base_string = "+abcde-"
        result = mod.do_join_args_kwargs(base_string, "")
        assert result == ""

        # Tainted joiner
        tainted_base_string = taint_pyobject(
            pyobject="+abcde-",
            source_name="joiner",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_args_kwargs(tainted_base_string, "")
        assert result == ""

        ranges = get_tainted_ranges(result)
        assert len(ranges) == 0

    def test_string_join_empty_joiner_arg_tainted(self):
        # type: () -> None
        # Not tainted
        base_string = ""
        result = mod.do_join_args_kwargs(base_string, "fghi")
        assert result == "fghi"

        # Tainted iterable
        tainted_fghi = taint_pyobject(
            pyobject="fghi",
            source_name="fghi",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_args_kwargs(base_string, tainted_fghi)
        assert result == "fghi"

        ranges = get_tainted_ranges(result)
        assert len(ranges) == 1
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "fghi"

    def test_string_join_iterable_tainted(self):
        # type: () -> None
        # Not tainted
        base_string = "+abcde-"
        result = mod.do_join_args_kwargs(base_string, "fg")
        assert result == "f+abcde-g"

        # Tainted iterable
        tainted_fg = taint_pyobject(
            pyobject="fg",
            source_name="fg",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_args_kwargs(base_string, tainted_fg)
        assert result == "f+abcde-g"

        ranges = get_tainted_ranges(result)
        assert len(ranges) == 2
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "f"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "g"

    def test_string_join_iterable_first_half_tainted(self):
        # type: () -> None
        # Not tainted
        base_string = "-abcde-"
        result = mod.do_join_args_kwargs(base_string, "fg")
        assert result == "f-abcde-g"

        # Tainted iterable
        tainted_fg = taint_pyobject(
            pyobject="fg",
            source_name="fg",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_args_kwargs(base_string, tainted_fg)
        assert result == "f-abcde-g"

        ranges = get_tainted_ranges(result)
        assert len(ranges) == 2
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "f"

    def test_string_join_iterable_second_half_tainted(self):
        # type: () -> None
        # Not tainted
        base_string = "-abcde-"
        result = mod.do_join_args_kwargs(base_string, "fg")
        assert result == "f-abcde-g"

        # Tainted iterable
        tainted_fg = taint_pyobject(
            pyobject="fg",
            source_name="fg",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_args_kwargs(base_string, tainted_fg)
        assert result == "f-abcde-g"

        ranges = get_tainted_ranges(result)
        assert len(ranges) == 2
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "f"

    def test_string_join_iterable_middle_tainted(self):
        # type: () -> None
        # Not tainted
        base_string = "+abcde-"
        result = mod.do_join_args_kwargs(base_string, "fgh")
        assert result == "f+abcde-g+abcde-h"

        # Tainted iterable
        tainted_fgh = taint_pyobject(
            pyobject="fgh",
            source_name="fgh",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_args_kwargs(base_string, tainted_fgh)
        assert result == "f+abcde-g+abcde-h"

        ranges = get_tainted_ranges(result)
        assert len(ranges) == 3
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "f"

    def test_string_join_joiner_tainted(self):
        # type: () -> None
        # Tainted joiner
        tainted_base_string = taint_pyobject(
            pyobject="-abcde-",
            source_name="joiner",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_args_kwargs(tainted_base_string, "fg")
        assert result == "f-abcde-g"

        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "-abcde-"

    def test_string_join_all_tainted(self):
        # type: () -> None
        # Tainted joiner
        tainted_base_string = taint_pyobject(
            pyobject="+abcde-",
            source_name="joiner",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        tainted_fghi = taint_pyobject(
            pyobject="fghi",
            source_name="fghi",
            source_value="foo",
            source_origin=OriginType.PARAMETER,
        )
        result = mod.do_join_args_kwargs(tainted_base_string, tainted_fghi)
        assert result == "f+abcde-g+abcde-h+abcde-i"

        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "f"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "+abcde-"
        assert result[ranges[2].start : (ranges[2].start + ranges[2].length)] == "g"
        assert result[ranges[3].start : (ranges[3].start + ranges[3].length)] == "+abcde-"
        assert result[ranges[4].start : (ranges[4].start + ranges[4].length)] == "h"
        assert result[ranges[5].start : (ranges[5].start + ranges[5].length)] == "+abcde-"
        assert result[ranges[6].start : (ranges[6].start + ranges[6].length)] == "i"
