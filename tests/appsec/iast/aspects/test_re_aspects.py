import re
import typing

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import index_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_expand_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_findall_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_finditer_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_fullmatch_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_group_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_groups_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_match_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_search_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_sub_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_subn_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import split_aspect
from tests.appsec.iast.iast_utils import _iast_patched_module


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods_py3")


def test_re_findall_aspect_tainted_string():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baaz.jpeg",
        source_name="test_re_findall_aspect_tainted_string",
        source_value="/foo/bar/baaz.jpeg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"[/.][a-z]*")

    added = add_aspect("/polompos/pok.jpeg", tainted_foobarbaz)
    res_list = re_findall_aspect(None, 1, re_slash, added)
    assert res_list == ["/polompos", "/pok", ".jpeg", "/foo", "/bar", "/baaz", ".jpeg"]
    for i in res_list:
        assert get_tainted_ranges(i) == [
            TaintRange(0, len(i), Source("test_re_sub_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)),
        ]


def test_re_findall_aspect_not_tainted():
    not_tainted_foobarbaz = "/foo/bar/baaz.jpeg"

    re_slash = re.compile(r"[/.][a-z]*")

    res_list = re_findall_aspect(None, 1, re_slash, not_tainted_foobarbaz)
    assert res_list == ["/foo", "/bar", "/baaz", ".jpeg"]
    for i in res_list:
        assert not is_pyobject_tainted(i)


def test_re_sub_aspect_tainted_string():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_re_sub_aspect_tainted_string",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    added = add_aspect("/polompos/pok", tainted_foobarbaz)
    res_str = re_sub_aspect(None, 1, re_slash, "_", added)
    assert res_str == "_polompos_pok_foo_bar_baz.jpg"
    assert get_tainted_ranges(res_str) == [
        TaintRange(
            13, len(res_str), Source("test_re_sub_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)
        ),
    ]


def test_re_sub_aspect_tainted_string_wrong_expression():
    tainted_foobarbaz = taint_pyobject(
        pyobject="test [1]",
        source_name="test_re_sub_aspect_tainted_string",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r".*\d]")
    res_no_tainted = re_slash.sub("", tainted_foobarbaz)
    # this function raises:
    # copy_and_shift_ranges_from_strings(string, result, 0, len(result))
    # ValueError: Error: Length cannot be set to 0.
    res_str = re_sub_aspect(None, 1, re_slash, "", tainted_foobarbaz)
    assert res_str == res_no_tainted


def test_re_sub_aspect_tainted_repl():
    tainted___ = taint_pyobject(
        pyobject="___",
        source_name="test_re_sub_aspect_tainted_repl",
        source_value="___",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_str = re_sub_aspect(None, 1, re_slash, tainted___, "foo/bar/baz")
    assert res_str == "foo___bar___baz"
    assert get_tainted_ranges(res_str) == [
        TaintRange(0, len(res_str), Source("test_re_sub_aspect_tainted_repl", tainted___, OriginType.PARAMETER)),
    ]


def test_re_sub_aspect_not_tainted():
    not_tainted___ = "___"
    re_slash = re.compile(r"/")
    res_str = re_sub_aspect(None, 1, re_slash, not_tainted___, "foo/bar/baz")
    assert res_str == "foo___bar___baz"
    assert not is_pyobject_tainted(res_str)


def test_re_subn_aspect_tainted_string():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_re_subn_aspect_tainted_string",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_str, number = re_subn_aspect(None, 1, re_slash, "_", tainted_foobarbaz)
    assert res_str == "_foo_bar_baz.jpg"
    assert number == 3
    assert get_tainted_ranges(res_str) == [
        TaintRange(
            0, len(res_str), Source("test_re_subn_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)
        ),
    ]


def test_re_subn_aspect_tainted_repl():
    tainted___ = taint_pyobject(
        pyobject="___",
        source_name="test_re_subn_aspect_tainted_repl",
        source_value="___",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_str, number = re_subn_aspect(None, 1, re_slash, tainted___, "foo/bar/baz")
    assert res_str == "foo___bar___baz"
    assert number == 2
    assert get_tainted_ranges(res_str) == [
        TaintRange(0, len(res_str), Source("test_re_subn_aspect_tainted_repl", tainted___, OriginType.PARAMETER)),
    ]


def test_re_subn_aspect_not_tainted():
    not_tainted___ = "___"
    re_slash = re.compile(r"/")
    res_str, number = re_subn_aspect(None, 1, re_slash, not_tainted___, "foo/bar/baz")
    assert res_str == "foo___bar___baz"
    assert number == 2
    assert not is_pyobject_tainted(res_str)


def test_re_split_aspect_not_tainted_re_object():
    not_tainted_foobarbaz = "/foo/bar/baz.jpg"

    re_slash = re.compile(r"/")

    res_list = split_aspect(None, 1, re_slash, not_tainted_foobarbaz)
    assert res_list == ["", "foo", "bar", "baz.jpg"]
    for res_str in res_list:
        assert not is_pyobject_tainted(res_str)


def test_re_split_aspect_not_tainted_re_module():
    not_tainted_foobarbaz = "/foo/bar/baz.jpg"

    res_list = split_aspect(None, 1, re, r"/", not_tainted_foobarbaz)
    assert res_list == ["", "foo", "bar", "baz.jpg"]
    for res_str in res_list:
        assert not is_pyobject_tainted(res_str)


def test_re_split_aspect_tainted_string_re_object():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_re_split_aspect_tainted_string",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_list = split_aspect(None, 1, re_slash, tainted_foobarbaz)
    assert res_list == ["", "foo", "bar", "baz.jpg"]
    for res_str in res_list:
        if len(res_str):
            assert get_tainted_ranges(res_str) == [
                TaintRange(
                    0,
                    len(res_str),
                    Source("test_re_split_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER),
                ),
            ]
        else:
            assert not is_pyobject_tainted(res_str)


def test_re_split_aspect_tainted_string_re_module():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_re_split_aspect_tainted_string",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    res_list = split_aspect(None, 1, re, r"/", tainted_foobarbaz)
    assert res_list == ["", "foo", "bar", "baz.jpg"]
    for res_str in res_list:
        if len(res_str):
            assert get_tainted_ranges(res_str) == [
                TaintRange(
                    0,
                    len(res_str),
                    Source("test_re_split_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER),
                ),
            ]
        else:
            assert not is_pyobject_tainted(res_str)


def test_re_match_aspect_not_tainted_re_object():
    not_tainted_isaac_newton = "Isaac Newton, physicist"

    re_obj = re.compile(r"(\w+) (\w+)")

    re_match = re_match_aspect(None, 1, re_obj, not_tainted_isaac_newton)
    result = re_groups_aspect(None, 1, re_match)
    assert result == ("Isaac", "Newton")
    for res_str in result:
        assert not is_pyobject_tainted(res_str)


def test_re_match_aspect_tainted_string_re_object():
    tainted_isaac_newton = taint_pyobject(
        pyobject="Isaac Newton, physicist",
        source_name="test_re_match_groups_aspect_tainted_string",
        source_value="Isaac Newton, physicist",
        source_origin=OriginType.PARAMETER,
    )

    re_obj = re.compile(r"(\w+) (\w+), (\w+) (\w+). (\w+) (\w+)")

    added = add_aspect("Winston Wolfe, problem solver. ", tainted_isaac_newton)
    re_match = re_match_aspect(None, 1, re_obj, added)
    result = re_groups_aspect(None, 1, re_match)
    assert result == ("Winston", "Wolfe", "problem", "solver", "Isaac", "Newton")
    for res_str in result:
        if len(res_str):
            ranges = get_tainted_ranges(res_str)
            assert ranges == [
                TaintRange(
                    0,
                    len(res_str),
                    Source("test_re_match_groups_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
                ),
            ]
        else:
            assert not is_pyobject_tainted(res_str)


def test_re_match_expand_aspect_tainted_string_re_object():
    tainted_isaac_newton = taint_pyobject(
        pyobject="Isaac Newton, physicist",
        source_name="test_re_match_group_aspect_tainted_string",
        source_value="Isaac Newton",
        source_origin=OriginType.PARAMETER,
    )

    re_obj = re.compile(r"(\w+) (\w+) (\w+) (\w+)")

    added = add_aspect("Winston Wolfe ", tainted_isaac_newton)
    re_match = re_match_aspect(None, 1, re_obj, added)
    result = re_expand_aspect(None, 1, re_match, "Name: \\1, Surname: \\2, Name: \\3, Surname: \\4")
    assert result == "Name: Winston, Surname: Wolfe, Name: Isaac, Surname: Newton"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
        ),
    ]


def test_re_match_expand_aspect_not_tainted_re_object():
    not_tainted_isaac_newton = "Isaac Newton, physicist"

    re_obj = re.compile(r"(\w+) (\w+)")

    re_match = re_match_aspect(None, 1, re_obj, not_tainted_isaac_newton)
    result = re_expand_aspect(None, 1, re_match, "Name: \\1, Surname: \\2")
    assert result == "Name: Isaac, Surname: Newton"
    assert not is_pyobject_tainted(result)


def test_re_match_expand_aspect_tainted_template_re_object():
    re_obj = re.compile(r"(\w+) (\w+)")
    re_match = re_match_aspect(None, 1, re_obj, "Isaac Newton, physicist")

    tainted_template = taint_pyobject(
        pyobject="Name: \\1, Surname: \\2",
        source_name="test_re_match_group_aspect_tainted_string",
        source_value="Name: \\1, Surname: \\2",
        source_origin=OriginType.PARAMETER,
    )

    result = re_expand_aspect(None, 1, re_match, tainted_template)
    assert result == "Name: Isaac, Surname: Newton"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_template, OriginType.PARAMETER),
        ),
    ]


def test_re_match_expand_aspect_not_tainted_template_re_object():
    re_obj = re.compile(r"(\w+) (\w+)")
    re_match = re_match_aspect(None, 1, re_obj, "Isaac Newton, physicist")

    not_tainted_template = "Name: \\1, Surname: \\2"
    result = re_expand_aspect(None, 1, re_match, not_tainted_template)
    assert result == "Name: Isaac, Surname: Newton"
    assert not is_pyobject_tainted(result)


def test_re_match_group_aspect_tainted_string_re_object():
    tainted_isaac_newton = taint_pyobject(
        pyobject="Isaac Newton, physicist",
        source_name="test_re_match_group_aspect_tainted_string",
        source_value="Isaac Newton, physicist",
        source_origin=OriginType.PARAMETER,
    )

    re_obj = re.compile(r"(\w+) (\w+), (\w+) (\w+). (\w+) (\w+), (\w+)")

    added = add_aspect("Winston Wolfe, problem solver. ", tainted_isaac_newton)
    re_match = re_match_aspect(None, 1, re_obj, added)
    result = re_group_aspect(None, 1, re_match, 1)
    assert result == "Winston"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
        ),
    ]
    result = re_group_aspect(None, 1, re_match, 2)
    assert result == "Wolfe"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
        ),
    ]
    result = re_group_aspect(None, 1, re_match, 3)
    assert result == "problem"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
        ),
    ]
    result = re_group_aspect(None, 1, re_match, 4)
    assert result == "solver"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
        ),
    ]
    result = re_group_aspect(None, 1, re_match, 5)
    assert result == "Isaac"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
        ),
    ]
    result = re_group_aspect(None, 1, re_match, 6)
    assert result == "Newton"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
        ),
    ]
    result = re_group_aspect(None, 1, re_match, 7)
    assert result == "physicist"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
        ),
    ]


def test_re_match_group_aspect_not_tainted_re_object():
    not_tainted_isaac_newton = "Isaac Newton, physicist"

    re_obj = re.compile(r"(\w+) (\w+)")

    re_match = re_match_aspect(None, 1, re_obj, not_tainted_isaac_newton)
    result = re_group_aspect(None, 1, re_match, 1)
    assert result == "Isaac"
    assert not is_pyobject_tainted(result)


def test_re_search_aspect_tainted_string_re_module():
    tainted_isaac_newton = taint_pyobject(
        pyobject="Isaac Newton, physicist",
        source_name="test_re_search_group_aspect_tainted_string",
        source_value="Isaac Newton, physicist",
        source_origin=OriginType.PARAMETER,
    )

    re_search = re_search_aspect(None, 1, re, r"(\w+) (\w+)", tainted_isaac_newton)
    result = re_groups_aspect(None, 1, re_search)
    assert result == ("Isaac", "Newton")
    for res_str in result:
        if len(res_str):
            assert get_tainted_ranges(res_str) == [
                TaintRange(
                    0,
                    len(res_str),
                    Source("test_re_search_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
                ),
            ]
        else:
            assert not is_pyobject_tainted(res_str)


def test_re_search_aspect_tainted_bytes_re_module():
    tainted_isaac_newton = taint_pyobject(
        pyobject=b"Isaac Newton, physicist",
        source_name="test_re_search_group_aspect_tainted_string",
        source_value="Isaac Newton, physicist",
        source_origin=OriginType.PARAMETER,
    )

    re_search = re_search_aspect(None, 1, re, rb"(\w+) (\w+)", tainted_isaac_newton)
    result = re_groups_aspect(None, 1, re_search)
    assert result == (b"Isaac", b"Newton")
    for res_str in result:
        if len(res_str):
            assert get_tainted_ranges(res_str) == [
                TaintRange(
                    0,
                    len(res_str),
                    Source(
                        "test_re_search_group_aspect_tainted_string", "Isaac Newton, physicist", OriginType.PARAMETER
                    ),
                ),
            ]
        else:
            assert not is_pyobject_tainted(res_str)


def test_re_search_aspect_not_tainted_re_module():
    not_tainted_isaac_newton = "Isaac Newton, physicist"

    re_search = re_search_aspect(None, 1, re, r"(\w+) (\w+)", not_tainted_isaac_newton)
    result = re_groups_aspect(None, 1, re_search)
    assert result == ("Isaac", "Newton")
    for i in result:
        assert not is_pyobject_tainted(i)


def test_re_search_aspect_tainted_string_re_object():
    tainted_isaac_newton = taint_pyobject(
        pyobject="Isaac Newton, physicist",
        source_name="test_re_search_groups_aspect_tainted_string",
        source_value="Isaac Newton, physicist",
        source_origin=OriginType.PARAMETER,
    )

    re_obj = re.compile(r"(\w+) (\w+)")

    re_search = re_search_aspect(None, 1, re_obj, tainted_isaac_newton)
    result = re_groups_aspect(None, 1, re_search)
    assert result == ("Isaac", "Newton")
    for res_str in result:
        if len(res_str):
            assert get_tainted_ranges(res_str) == [
                TaintRange(
                    0,
                    len(res_str),
                    Source("test_re_search_groups_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
                ),
            ]
        else:
            assert not is_pyobject_tainted(res_str)


def test_re_search_aspect_not_tainted_re_object():
    not_tainted_isaac_newton = "Isaac Newton, physicist"

    re_obj = re.compile(r"(\w+) (\w+)")

    re_search = re_search_aspect(None, 1, re_obj, not_tainted_isaac_newton)
    result = re_groups_aspect(None, 1, re_search)
    assert result == ("Isaac", "Newton")
    for i in result:
        assert not is_pyobject_tainted(i)


def test_re_fullmatch_aspect_tainted_string_re_object():
    tainted_isaac_newton = taint_pyobject(
        pyobject="Isaac Newton",
        source_name="test_re_fullmatch_groups_aspect_tainted_string",
        source_value="Isaac Newton",
        source_origin=OriginType.PARAMETER,
    )

    re_obj = re.compile(r"(\w+) (\w+)")

    re_fullmatch = re_fullmatch_aspect(None, 1, re_obj, tainted_isaac_newton)
    result = re_groups_aspect(None, 1, re_fullmatch)
    assert result == ("Isaac", "Newton")
    for res_str in result:
        if len(res_str):
            assert get_tainted_ranges(res_str) == [
                TaintRange(
                    0,
                    len(res_str),
                    Source(
                        "test_re_fullmatch_groups_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER
                    ),
                ),
            ]
        else:
            assert not is_pyobject_tainted(res_str)


def test_re_fullmatch_aspect_not_tainted_re_object():
    not_tainted_isaac_newton = "Isaac Newton"
    re_obj = re.compile(r"(\w+) (\w+)")

    re_fullmatch = re_fullmatch_aspect(None, 1, re_obj, not_tainted_isaac_newton)
    result = re_groups_aspect(None, 1, re_fullmatch)
    assert result == ("Isaac", "Newton")
    for res_str in result:
        assert not is_pyobject_tainted(res_str)


def test_re_finditer_aspect_tainted_string():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baaz.jpeg",
        source_name="test_re_finditer_aspect_tainted_string",
        source_value="/foo/bar/baaz.jpeg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"[/.][a-z]*")

    res_iterator = re_finditer_aspect(None, 1, re_slash, tainted_foobarbaz)
    assert isinstance(res_iterator, typing.Iterator), f"res_iterator is of type {type(res_iterator)}"
    try:
        tainted_item = next(res_iterator)
        assert get_tainted_ranges(tainted_item) == [
            TaintRange(0, 18, Source("test_re_sub_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)),
        ]
    except StopIteration:
        pytest.fail("re_finditer_aspect result generator is depleted")

    for i in res_iterator:
        assert get_tainted_ranges(i) == [
            TaintRange(0, 18, Source("test_re_sub_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)),
        ]


def test_re_finditer_aspect_tainted_bytes():
    tainted_multipart = taint_pyobject(
        pyobject=b' name="files"; filename="test.txt"\r\nContent-Type: text/plain',
        source_name="test_re_finditer_aspect_tainted_string",
        source_value="/foo/bar/baaz.jpeg",
        source_origin=OriginType.PARAMETER,
    )
    SPECIAL_CHARS = re.escape(b'()<>@,;:\\"/[]?={} \t')
    QUOTED_STR = rb'"(?:\\.|[^"])*"'
    VALUE_STR = rb"(?:[^" + SPECIAL_CHARS + rb"]+|" + QUOTED_STR + rb")"
    OPTION_RE_STR = rb"(?:;|^)\s*([^" + SPECIAL_CHARS + rb"]+)\s*=\s*(" + VALUE_STR + rb")"
    OPTION_RE = re.compile(OPTION_RE_STR)
    res_no_tainted = OPTION_RE.finditer(tainted_multipart)
    res_iterator = re_finditer_aspect(None, 1, OPTION_RE, tainted_multipart)
    assert isinstance(res_iterator, typing.Iterator), f"res_iterator is of type {type(res_iterator)}"

    res_list = list(res_no_tainted)
    assert res_list[0].group(0) == b' name="files"'
    assert res_list[1].group(0) == b'; filename="test.txt"'

    try:
        tainted_item = next(res_iterator)
        ranges = get_tainted_ranges(tainted_item)
        assert ranges == [
            TaintRange(0, 60, Source("test_re_sub_aspect_tainted_string", tainted_multipart, OriginType.PARAMETER)),
        ]
    except StopIteration:
        pytest.fail("re_finditer_aspect result generator is depleted")

    for i in res_iterator:
        assert i.group(0) == b'; filename="test.txt"'
        assert get_tainted_ranges(i) == [
            TaintRange(0, 60, Source("test_re_sub_aspect_tainted_string", tainted_multipart, OriginType.PARAMETER)),
        ]


def test_re_finditer_aspect_not_tainted():
    not_tainted_foobarbaz = "/foo/bar/baaz.jpeg"

    re_slash = re.compile(r"[/.][a-z]*")

    res_iterator = re_finditer_aspect(None, 1, re_slash, not_tainted_foobarbaz)
    assert isinstance(res_iterator, typing.Iterator)

    try:
        tainted_item = next(res_iterator)
        assert not is_pyobject_tainted(tainted_item)
    except StopIteration:
        pytest.fail("re_finditer_aspect result generator is finished")
    for i in res_iterator:
        assert not is_pyobject_tainted(i)


def test_re_match_getitem_aspect_tainted_string_re_object():
    tainted_isaac_newton = taint_pyobject(
        pyobject="Isaac Newton, physicist",
        source_name="test_re_match_groups_aspect_tainted_string",
        source_value="Isaac Newton, physicist",
        source_origin=OriginType.PARAMETER,
    )

    re_obj = re.compile(r"(\w+) (\w+)")

    re_match = re_match_aspect(None, 1, re_obj, tainted_isaac_newton)

    isaac_newton = index_aspect(re_match, 0)
    assert isaac_newton == "Isaac Newton"

    isaac = index_aspect(re_match, 1)
    assert isaac == "Isaac"

    newton = index_aspect(re_match, 2)
    assert newton == "Newton"

    assert is_pyobject_tainted(isaac_newton)
    assert is_pyobject_tainted(isaac)
    assert is_pyobject_tainted(newton)


def test_re_match_getitem_aspect_not_tainted_string_re_object():
    not_tainted_isaac_newton = "Isaac Newton, physicist"

    re_obj = re.compile(r"(\w+) (\w+)")

    re_match = re_match_aspect(None, 1, re_obj, not_tainted_isaac_newton)

    isaac_newton = index_aspect(re_match, 0)
    assert isaac_newton == "Isaac Newton"

    isaac = index_aspect(re_match, 1)
    assert isaac == "Isaac"

    newton = index_aspect(re_match, 2)
    assert newton == "Newton"

    assert not is_pyobject_tainted(isaac_newton)
    assert not is_pyobject_tainted(isaac)
    assert not is_pyobject_tainted(newton)


@pytest.mark.parametrize(
    "input_str, expected_result, tainted",
    [
        ("print('Hello, world!')", "print", True),
    ],
)
def test_match_group_complex(input_str, expected_result, tainted):
    regex = re.compile(
        r"(?<!\.)(__import__|a(?:bs|iter|ll|ny)|b(?:in|ool|reakpoint|yte(?:array|s))|c(?:allable|hr|lassmethod|"
        r"omp(?:ile|lex))|d(?:elattr|i(?:ct|r|vmod))|e(?:numerate|val)|f(?:ilter|(?:loa|orma|rozense)t)|"
        r"g(?:etattr|lobals)|h(?:as(?:attr|h)|ex)|i(?:d|n(?:(?:(?:pu)?)t)|s(?:instance|subclass)|ter)|"
        r"l(?:en|ist|ocals)|m(?:a(?:[px])|emoryview|in)|next|o(?:bject|ct|pen|rd)|p(?:ow|r(?:int|"  # codespell:ignore
        r"operty))|r(?:ange|e(?:pr|versed)|ound)|s(?:et(?:(?:attr)?)|lice|orted|t(?:aticmethod|r)|"
        r"u(?:m|per))|t(?:(?:upl|yp)e)|vars|zip)\b",
        re.MULTILINE,
    )

    matches = regex.match(input_str, 0)

    input_tainted = taint_pyobject(
        pyobject=input_str,
        source_name="test_add_aspect_tainting_left_hand",
        source_value=input_str,
        source_origin=OriginType.PARAMETER,
    )
    result = mod.do_match_group(input_tainted)
    assert result == matches.group() == expected_result
    assert is_pyobject_tainted(result) is tainted
