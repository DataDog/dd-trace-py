import re
import typing

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
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


def test_re_findall_aspect_tainted_string():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baaz.jpeg",
        source_name="test_re_findall_aspect_tainted_string",
        source_value="/foo/bar/baaz.jpeg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"[/.][a-z]*")

    res_list = re_findall_aspect(None, 1, re_slash, tainted_foobarbaz)
    assert res_list == ["/foo", "/bar", "/baaz", ".jpeg"]
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

    res_str = re_sub_aspect(None, 1, re_slash, "_", tainted_foobarbaz)
    assert res_str == "_foo_bar_baz.jpg"
    assert get_tainted_ranges(res_str) == [
        TaintRange(
            0, len(res_str), Source("test_re_sub_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)
        ),
    ]


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

    re_obj = re.compile(r"(\w+) (\w+)")

    re_match = re_match_aspect(None, 1, re_obj, tainted_isaac_newton)
    result = re_groups_aspect(None, 1, re_match)
    assert result == ("Isaac", "Newton")
    for res_str in result:
        if len(res_str):
            assert get_tainted_ranges(res_str) == [
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
        source_value="Isaac Newton, physicist",
        source_origin=OriginType.PARAMETER,
    )

    re_obj = re.compile(r"(\w+) (\w+)")

    re_match = re_match_aspect(None, 1, re_obj, tainted_isaac_newton)
    result = re_expand_aspect(None, 1, re_match, "Name: \\1, Surname: \\2")
    assert result == "Name: Isaac, Surname: Newton"
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

    re_obj = re.compile(r"(\w+) (\w+)")

    re_match = re_match_aspect(None, 1, re_obj, tainted_isaac_newton)
    result = re_group_aspect(None, 1, re_match, 1)
    assert result == "Isaac"
    assert get_tainted_ranges(result) == [
        TaintRange(
            0,
            len(result),
            Source("test_re_match_group_aspect_tainted_string", tainted_isaac_newton, OriginType.PARAMETER),
        ),
    ]
    result = re_group_aspect(None, 1, re_match, 2)
    assert result == "Newton"
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
    assert isinstance(res_iterator, typing.Iterator)
    for i in res_iterator:
        assert get_tainted_ranges(i) == [
            TaintRange(0, len(i), Source("test_re_sub_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)),
        ]


def test_re_finditer_aspect_not_tainted():
    not_tainted_foobarbaz = "/foo/bar/baaz.jpeg"

    re_slash = re.compile(r"[/.][a-z]*")

    res_iterator = re_finditer_aspect(None, 1, re_slash, not_tainted_foobarbaz)
    assert isinstance(res_iterator, typing.Iterator)
    for i in res_iterator:
        assert not is_pyobject_tainted(i)
