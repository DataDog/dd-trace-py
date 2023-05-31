#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
import pytest

try:
    from tests.appsec.iast.aspects.conftest import _iast_patched_module
    from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject
    from ddtrace.appsec.iast._taint_tracking import Source
    from ddtrace.appsec.iast._taint_tracking import OriginType
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)

mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")


class TestOperatorJoinReplacement(object):
    def test_string_join_tainted_joiner(self):  # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            "-joiner-", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 1, 3
        )
        it = ["a", "b", "c"]

        result = mod.do_join(string_input, it)
        assert result == "a-joiner-b-joiner-c"
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "joi"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "joi"

    def test_string_join_tainted_joiner_bytes(self):  # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            b"-joiner-", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 1, 3
        )
        it = [b"a", b"b", b"c"]
        result = mod.do_join(string_input, it)
        assert result == b"a-joiner-b-joiner-c"
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == b"joi"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == b"joi"

    def test_string_join_tainted_joiner_bytes_bytearray(self):  # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            b"-joiner-", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 1, 3
        )
        it = [bytearray(b"a"), bytearray(b"b"), bytearray(b"c")]
        result = mod.do_join(string_input, it)
        assert result == b"a-joiner-b-joiner-c"
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == b"joi"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == b"joi"

    def test_string_join_tainted_joiner_bytearray(self):  # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            bytearray(b"-joiner-"), Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 1, 3
        )
        it = [bytearray(b"a"), bytearray(b"b"), bytearray(b"c")]

        result = mod.do_join(string_input, it)
        assert result == bytearray(b"a-joiner-b-joiner-c")
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == bytearray(b"joi")
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == bytearray(b"joi")

    def test_string_join_tainted_joiner_bytearray_bytes(self):  # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        # taint "joi" from "-joiner-"
        string_input = taint_pyobject(
            bytearray(b"-joiner-"), Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 1, 3
        )
        it = [b"a", b"b", b"c"]

        result = mod.do_join(string_input, it)
        assert result == bytearray(b"a-joiner-b-joiner-c")
        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == bytearray(b"joi")
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == bytearray(b"joi")

    def test_string_join_tainted_joined(self):  # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)
        string_input = "-joiner-"
        it = [
            taint_pyobject("aaaa", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 3),
            "bbbb",
            taint_pyobject("cccc", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 3),
        ]

        result = mod.do_join(string_input, it)
        ranges = get_tainted_ranges(result)
        assert result == "aaaa-joiner-bbbb-joiner-cccc"
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "aaa"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "ccc"

    def test_string_join_tainted_all(self):  # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        string_input = taint_pyobject(
            "-joiner-", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 1, 2
        )
        it = [
            taint_pyobject("aaaa", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 1),
            "bbbb",
            taint_pyobject("cccc", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 4),
            taint_pyobject("dddd", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 3),
            taint_pyobject("eeee", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 2),
            taint_pyobject("ffff", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 3),
            taint_pyobject("gggg", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 4),
        ]

        result = mod.do_join(string_input, it)
        ranges = get_tainted_ranges(result)
        assert result == "aaaa-joiner-bbbb-joiner-cccc-joiner-dddd-joiner-eeee-joiner-ffff-joiner-gggg"
        pos = 0
        for results in ("a", "jo", "jo", "cccc", "jo", "ddd", "jo", "ee", "jo", "fff", "jo", "gggg"):
            assert result[ranges[pos].start : (ranges[pos].start + ranges[pos].length)] == results
            pos += 1

    def test_string_join_tuple(self):  # type: () -> None
        # Not tainted
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        base_string = "abcde"
        result = mod.do_join_tuple(base_string)
        assert result == "abcde1abcde2abcde3"
        assert not get_tainted_ranges(result)

        # Tainted
        tainted_base_string = taint_pyobject(
            "abcde", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 3
        )
        result = mod.do_join_tuple(tainted_base_string)
        assert result == "abcde1abcde2abcde3"

        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "abc"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "abc"
        assert result[ranges[2].start : (ranges[2].start + ranges[2].length)] == "abc"

    def test_string_join_set(self):  # type: () -> None
        # Not tainted
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        base_string = "abcde"
        result = mod.do_join_set(base_string)
        # assert result == "abcde2abcde1abcde3"
        assert not get_tainted_ranges(result)

        # Tainted
        tainted_base_string = taint_pyobject(
            "abcde", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 3
        )
        result = mod.do_join_set(tainted_base_string)
        # assert result == "abcde2abcde1abcde3"

        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "abc"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "abc"
        assert result[ranges[2].start : (ranges[2].start + ranges[2].length)] == "abc"

    def test_string_join_generator(self):
        # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)
        # Not tainted
        base_string = "abcde"
        result = mod.do_join_generator(base_string)
        assert result == "abcdeabcdeabcde"
        assert not get_tainted_ranges(result)

        # Tainted
        tainted_base_string = taint_pyobject(
            "abcde", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 3
        )
        result = mod.do_join_generator(tainted_base_string)
        assert result == "abcdeabcdeabcde"

        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "abc"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "abc"
        assert result[ranges[2].start : (ranges[2].start + ranges[2].length)] == "abc"

    def test_string_join_yield(self):
        # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)
        # Not tainted
        base_string = "abcde"
        result = mod.do_join_generator_2(base_string)
        assert result == "xabcdeyabcdez"

        # Tainted
        tainted_base_string = taint_pyobject(
            "abcde", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER), 0, 3
        )
        result = mod.do_join_generator_2(tainted_base_string)
        assert result == "xabcdeyabcdez"

        ranges = get_tainted_ranges(result)
        assert result[ranges[0].start : (ranges[0].start + ranges[0].length)] == "abc"
        assert result[ranges[1].start : (ranges[1].start + ranges[1].length)] == "abc"
