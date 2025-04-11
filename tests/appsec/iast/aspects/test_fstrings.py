import sys

from hypothesis import given
from hypothesis.strategies import builds
from hypothesis.strategies import integers
from hypothesis.strategies import sampled_from
from hypothesis.strategies import text
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
from tests.appsec.iast.aspects.aspect_utils import create_taint_range_with_format
from tests.appsec.iast.iast_utils import CustomStr
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import non_empty_text


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")
mod_py3 = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods_py3")


@given(builds(CustomStr, text()))
def test_fstring_custom_str(text):
    result = mod_py3.do_fstring(text)
    assert result == mod_py3.do_fstring(text)
    assert result == f"{text}"


@given(text())
def test_fstring(text):
    result = mod_py3.do_fstring(text)
    assert result == mod_py3.do_fstring(text)
    assert result == f"{text}"


@given(non_empty_text)
def test_fstring_tainted(text):
    string_input = taint_pyobject(
        pyobject=text, source_name="foo", source_value=text, source_origin=OriginType.PARAMETER
    )
    result = mod_py3.do_fstring(string_input)
    assert result == mod_py3.do_fstring(text)
    assert result == f"{text}"
    assert is_pyobject_tainted(result)


@given(non_empty_text)
def test_fstring_fill_spaces_tainted(text):
    string_input = taint_pyobject(
        pyobject=text, source_name="foo", source_value=text, source_origin=OriginType.PARAMETER
    )
    result = mod_py3.do_fmt_value(string_input)
    assert result == mod_py3.do_fmt_value(text)
    assert result == f"{text:<8s}bar"
    assert is_pyobject_tainted(result)


@given(
    integers(),
    sampled_from(
        [
            "<8s",
            "<1s",
        ]
    ),
)
def test_fstring_fill_spaces_integers_unkwow_format(text, spec):
    with pytest.raises(ValueError) as excinfo:
        f"{text:{spec}}bar"
    assert str(excinfo.value) == "Unknown format code 's' for object of type 'int'"

    with pytest.raises(ValueError) as excinfo:
        mod_py3.do_fmt_value(text, spec)
    assert str(excinfo.value) == "Unknown format code 's' for object of type 'int'"


@given(
    integers(),
    sampled_from(
        [
            "!s",
            "!s",
        ]
    ),
)
def test_fstring_fill_spaces_integers_invalid_format(text, spec):
    with pytest.raises(ValueError) as excinfo:
        f"{text:{spec}}bar"
    if sys.version_info >= (3, 11):
        assert str(excinfo.value) == "Invalid format specifier '!s' for object of type 'int'"
    else:
        assert str(excinfo.value) == "Invalid format specifier"

    with pytest.raises(ValueError) as excinfo:
        mod_py3.do_fmt_value(text, spec)

    if sys.version_info >= (3, 11):
        assert str(excinfo.value) == "Invalid format specifier '!s' for object of type 'int'"
    else:
        assert str(excinfo.value) == "Invalid format specifier"


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Python3.8 works different with fstrings")
@given(non_empty_text)
def test_repr_fstring_tainted(text):
    string_input = taint_pyobject(
        pyobject=text, source_name="foo", source_value=text, source_origin=OriginType.PARAMETER
    )
    result = mod_py3.do_repr_fstring(string_input)
    assert result == mod_py3.do_repr_fstring(text)
    assert result == f"{text!r}"
    assert is_pyobject_tainted(result)


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Python3.8 works different with fstrings")
@given(non_empty_text)
def test_repr_fstring_with_format_tainted(text):
    string_input = taint_pyobject(
        pyobject=text, source_name="foo", source_value=text, source_origin=OriginType.PARAMETER
    )
    result = mod_py3.do_repr_fstring_with_format(string_input)
    assert result == mod_py3.do_repr_fstring_with_format(text)
    assert result == f"{text!r:10}"
    assert is_pyobject_tainted(result)


@given(integers())
def test_int_fstring_zero_padding_integers(integers_to_test):
    result = mod_py3.do_zero_padding_fstring(integers_to_test)
    assert result == f"{integers_to_test:05d}"


@given(
    text(),
    sampled_from(
        [
            "d",  # decimal integer
            "f",  # float
            "e",  # scientific notation
            "g",  # general format
            "b",  # binary
            "o",  # octal
            "x",  # hexadecimal
            "X",  # uppercase hexadecimal
            "n",  # number with locale
        ]
    ),
)
def test_int_fstring_zero_padding_text(text, spec):
    print(f"{text}: {spec}")
    with pytest.raises(ValueError) as excinfo:
        f"{text:{spec}}"
    assert str(excinfo.value) == f"Unknown format code '{spec}' for object of type 'str'"

    with pytest.raises(ValueError) as excinfo:
        mod_py3.do_zero_padding_fstring(text, spec)
    assert str(excinfo.value) == f"Unknown format code '{spec}' for object of type 'str'"


class TestOperatorsReplacement(BaseReplacement):
    def test_string_build_string_tainted(self):
        string_input = "foo"
        result = mod_py3.do_fmt_value(string_input)  # pylint: disable=no-member
        assert result == "foo     bar"

        string_input = create_taint_range_with_format(":+-foo-+:")
        result = mod_py3.do_fmt_value(string_input)  # pylint: disable=no-member
        assert result == "foo     bar"
        assert as_formatted_evidence(result) == ":+-foo-+:     bar"

    def test_string_fstring_tainted(self):
        string_input = "foo"
        result = mod_py3.do_repr_fstring(string_input)
        assert result == "'foo'"

        string_input = create_taint_range_with_format(":+-foo-+:")

        result = mod_py3.do_repr_fstring(string_input)  # pylint: disable=no-member
        assert as_formatted_evidence(result) == "':+-foo-+:'"

    def test_string_fstring_with_format_tainted(self):
        string_input = "foo"
        result = mod_py3.do_repr_fstring_with_format(string_input)
        assert result == "'foo'     "

        string_input = create_taint_range_with_format(":+-foo-+:")

        result = mod_py3.do_repr_fstring_with_format(string_input)  # pylint: disable=no-member
        assert as_formatted_evidence(result) == "':+-foo-+:'     "

    def test_string_fstring_repr_str_twice_tainted(self):
        string_input = "foo"

        result = mod_py3.do_repr_fstring_twice(string_input)  # pylint: disable=no-member
        assert result == "'foo' 'foo'"

        string_input = create_taint_range_with_format(":+-foo-+:")

        result = mod_py3.do_repr_fstring_twice(string_input)  # pylint: disable=no-member
        assert result == "'foo' 'foo'"
        assert as_formatted_evidence(result) == "':+-foo-+:' ':+-foo-+:'"

    def test_string_fstring_repr_object_twice_tainted(self):
        string_input = "foo"
        result = mod.MyObject(string_input)
        assert repr(result) == "foo a"

        result = mod_py3.do_repr_fstring_twice(result)  # pylint: disable=no-member
        assert result == "foo a foo a"

        string_input = create_taint_range_with_format(":+-foo-+:")
        obj = mod.MyObject(string_input)  # pylint: disable=no-member

        result = mod_py3.do_repr_fstring_twice(obj)  # pylint: disable=no-member
        assert result == "foo a foo a"
        assert as_formatted_evidence(result) == ":+-foo-+: a :+-foo-+: a"

    def test_string_fstring_twice_different_objects_tainted(self):
        string_input = create_taint_range_with_format(":+-foo-+:")
        obj = mod.MyObject(string_input)  # pylint: disable=no-member
        obj2 = mod.MyObject(string_input)  # pylint: disable=no-member

        result = mod_py3.do_repr_fstring_twice_different_objects(obj, obj2)  # pylint: disable=no-member
        assert result == "foo a foo a"
        assert as_formatted_evidence(result) == ":+-foo-+: a :+-foo-+: a"

    def test_string_fstring_twice_different_objects_tainted_twice(self):
        string_input = create_taint_range_with_format(":+-foo-+:")
        obj = mod.MyObject(string_input)  # pylint: disable=no-member

        result = mod_py3.do_repr_fstring_with_format_twice(obj)  # pylint: disable=no-member
        assert result == "foo a      foo a      "
        assert as_formatted_evidence(result) == ":+-foo-+: a      :+-foo-+: a      "

    @pytest.mark.parametrize(
        "function",
        [
            mod_py3.do_repr_fstring_with_expression1,
            mod_py3.do_repr_fstring_with_expression2,
            mod_py3.do_repr_fstring_with_expression3,
            mod_py3.do_repr_fstring_with_expression4,
            mod_py3.do_repr_fstring_with_expression5,
        ],
    )
    def test_string_fstring_non_string(self, function):
        result = function()  # pylint: disable=no-member
        assert result == "Hello world, True!"
