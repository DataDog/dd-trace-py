import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject_with_ranges
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils_side_effects import MagicMethodsException


mod = _iast_patched_module("benchmarks.bm.iast_fixtures.str_methods")

STRING_TO_TAINT = "abc"


def test_radd_aspect_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    def __radd__(self, a):
        return self._data + a

    _old_method = getattr(MagicMethodsException, "__radd__", None)
    setattr(MagicMethodsException, "__radd__", __radd__)
    result = "123" + object_with_side_effects
    assert result == STRING_TO_TAINT + "123"

    result_tainted = ddtrace_aspects.add_aspect("123", object_with_side_effects)

    assert result_tainted == result

    setattr(MagicMethodsException, "__radd__", _old_method)


def test_add_aspect_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    def __add__(self, a):
        return self._data + a

    _old_method = getattr(MagicMethodsException, "__add__", None)
    setattr(MagicMethodsException, "__add__", __add__)
    result = object_with_side_effects + "123"
    assert result == "abc123"

    result_tainted = ddtrace_aspects.add_aspect(object_with_side_effects, "123")

    assert result_tainted == result

    setattr(MagicMethodsException, "__radd__", _old_method)


def test_split_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    result = mod.do_split_no_args(object_with_side_effects)
    assert result == ["abc"]


def test_split_side_effects_none():
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'split'"):
        mod.do_split_no_args(None)


def test_rsplit_side_effects():
    def __call__(self):
        return self._data

    _old_method = getattr(MagicMethodsException, "__call__", None)
    setattr(MagicMethodsException, "__call__", __call__)

    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    object_with_side_effects.rsplit = MagicMethodsException(STRING_TO_TAINT)

    result = object_with_side_effects.rsplit()
    assert result
    assert mod.do_rsplit_no_args(object_with_side_effects) == result

    setattr(MagicMethodsException, "__call__", _old_method)


def test_rsplit_side_effects_none():
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'rsplit'"):
        mod.do_rsplit_no_args(None)


def test_splitlines_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    with pytest.raises(AttributeError, match="'MagicMethodsException' object has no attribute 'splitlines'"):
        mod.do_splitlines_no_arg(object_with_side_effects)


def test_splitlines_side_effects_none():
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'splitlines'"):
        mod.do_splitlines_no_arg(None)


def test_str_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    with pytest.raises(Exception, match="side effect"):
        str(object_with_side_effects)

    with pytest.raises(Exception, match="side effect"):
        mod.do_str(object_with_side_effects)


def test_str_side_effects_none():
    result = mod.do_str(None)
    assert result == "None"


def test_bytes_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    with pytest.raises(Exception, match="side effect"):
        bytes(object_with_side_effects)

    with pytest.raises(Exception, match="side effect"):
        mod.do_bytes(object_with_side_effects)


def test_bytes_side_effects_none():
    with pytest.raises(TypeError, match="cannot convert 'NoneType' object to bytes"):
        bytes(None)

    with pytest.raises(TypeError, match="cannot convert 'NoneType' object to bytes"):
        mod.do_bytes(None)


def test_bytes_with_encoding_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    with pytest.raises(TypeError, match="encoding without a string argument"):
        bytes(object_with_side_effects, encoding="utf-8")

    with pytest.raises(TypeError, match="encoding without a string argument"):
        mod.do_str_to_bytes(object_with_side_effects)


def test_bytes_with_encoding_side_effects_none():
    with pytest.raises(TypeError, match="encoding without a string argument"):
        bytes(None, encoding="utf-8")

    with pytest.raises(TypeError, match="encoding without a string argument"):
        mod.do_str_to_bytes(None)


def test_bytearray_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    with pytest.raises(Exception, match="side effect"):
        bytearray(object_with_side_effects)

    with pytest.raises(Exception, match="side effect"):
        mod.do_bytes_to_bytearray(object_with_side_effects)


def test_bytearray_side_effects_none():
    msg = "cannot convert 'NoneType' object to bytearray"

    with pytest.raises(TypeError, match=msg):
        bytearray(None)

    with pytest.raises(TypeError, match=msg):
        mod.do_bytes_to_bytearray(None)


def test_join_aspect_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    it = ["a", "b", "c"]

    result = mod.do_join(object_with_side_effects, it)
    assert result == "abcabc"


def test_join_aspect_side_effects_iterables():
    it = [MagicMethodsException("a"), MagicMethodsException("b"), MagicMethodsException("c")]

    with pytest.raises(TypeError, match="sequence item 0: expected str instance, MagicMethodsException found"):
        STRING_TO_TAINT.join(it)
    with pytest.raises(TypeError, match="sequence item 0: expected str instance, MagicMethodsException found"):
        mod.do_join(STRING_TO_TAINT, it)


def test_index_aspect_side_effects():
    def __getitem__(self, a):
        return self._data[a]

    _old_method = getattr(MagicMethodsException, "__getitem__", None)
    setattr(MagicMethodsException, "__getitem__", __getitem__)

    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    result = object_with_side_effects[1]
    assert result == "b"

    result_tainted = mod.do_index(object_with_side_effects, 1)
    assert result_tainted == result
    setattr(MagicMethodsException, "__getitem__", _old_method)


def test_slice_aspect_side_effects():
    def __getitem__(self, a):
        return self._data[a]

    _old_method = getattr(MagicMethodsException, "__getitem__", None)
    setattr(MagicMethodsException, "__getitem__", __getitem__)

    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    result = object_with_side_effects[:2]
    assert result == "ab"

    result_tainted = mod.do_slice(object_with_side_effects, None, 2, None)
    assert result_tainted == result
    setattr(MagicMethodsException, "__getitem__", _old_method)


def test_bytearray_extend_side_effects():
    _old_method = getattr(MagicMethodsException, "__getitem__", None)
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    result = object_with_side_effects.extend(bytearray(b"abc"))
    assert result == bytearray(b"abcabc")

    mod.do_bytearray_extend(object_with_side_effects, bytearray(b"abc"))


def test_modulo_aspect_side_effects():
    def __str__(self):
        return self._data

    _old_method = getattr(MagicMethodsException, "__str__", None)
    setattr(MagicMethodsException, "__str__", __str__)

    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    result = "aa %s ss" % object_with_side_effects
    assert result == "aa %s ss" % "abc"

    result_tainted = mod.do_modulo("aa %s ss", object_with_side_effects)
    assert result_tainted == result

    setattr(MagicMethodsException, "__str__", _old_method)


def test_modulo_aspect_template_side_effects():
    def __str__(self):
        return self._data

    def __mod__(self, a):
        return self._data % a

    _old_method_str = getattr(MagicMethodsException, "__str__", None)
    _old_method_mod = getattr(MagicMethodsException, "__mod__", None)
    setattr(MagicMethodsException, "__str__", __str__)
    setattr(MagicMethodsException, "__mod__", __mod__)

    object_with_side_effects = MagicMethodsException("aa %s ss")
    result = object_with_side_effects % STRING_TO_TAINT
    assert result == "aa %s ss" % "abc"

    result_tainted = mod.do_modulo(object_with_side_effects, STRING_TO_TAINT)
    assert result_tainted == result

    setattr(MagicMethodsException, "__str__", _old_method_str)
    setattr(MagicMethodsException, "__mod__", _old_method_mod)


def test_ljust_aspect_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    result = object_with_side_effects.ljust(10)
    assert result == "abc       "

    result_tainted = mod.do_ljust(object_with_side_effects, 10)
    assert result_tainted == result


def test_ljust_aspect_fill_char_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    result = object_with_side_effects.ljust(10, "d")
    assert result == "abcddddddd"

    result_tainted = mod.do_ljust_2(object_with_side_effects, 10, "d")
    assert result_tainted == result


def test_zfill_aspect_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    result = object_with_side_effects.zfill(10)
    assert result == "0000000abc"

    result_tainted = mod.do_zfill(object_with_side_effects, 10)
    assert result_tainted == result


def test_format_aspect_side_effects():
    object_with_side_effects = MagicMethodsException("d: {}, e: {}")
    result = object_with_side_effects.format("f", STRING_TO_TAINT)
    assert result == "d: f, e: abc"

    result_tainted = mod.do_format(object_with_side_effects, "f", STRING_TO_TAINT)
    assert result_tainted == result


def test_format_not_str_side_effects():
    object_with_side_effects = MagicMethodsException("aaaa")

    result_tainted = mod.do_format_not_str(object_with_side_effects)
    assert result_tainted == "output"


def test_format_map_aspect_side_effects():
    object_with_side_effects = MagicMethodsException("d: {data1}, e: {data2}")
    result = object_with_side_effects.format_map(dict(data1=STRING_TO_TAINT, data2=STRING_TO_TAINT))
    assert result == "d: abc, e: abc"

    result_tainted = mod.do_format_map(object_with_side_effects, dict(data1=STRING_TO_TAINT, data2=STRING_TO_TAINT))
    assert result_tainted == result


def test_taint_pyobject():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    result = taint_pyobject(
        pyobject=object_with_side_effects,
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    assert result._data == STRING_TO_TAINT


def test_repr_aspect_side_effects():
    def __repr__(self):
        return self._data

    _old_method_repr = getattr(MagicMethodsException, "__repr__", None)
    setattr(MagicMethodsException, "__repr__", __repr__)

    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    result = repr(object_with_side_effects)
    assert result == STRING_TO_TAINT

    result_tainted = mod.do_repr(object_with_side_effects)
    assert result_tainted == result

    setattr(MagicMethodsException, "__repr__", _old_method_repr)


def test_format_value_aspect_side_effects():
    def __format__(self, *args, **kwargs):
        print(args)
        print(kwargs)
        return self._data

    def __add__(self, b):
        return self._data + b

    _old_method_format = getattr(MagicMethodsException, "__format__", None)
    setattr(MagicMethodsException, "__format__", __format__)
    setattr(MagicMethodsException, "__add__", __add__)

    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    b = "bar"
    result = f"{object_with_side_effects} + {b} = {object_with_side_effects + b}"
    assert result == "abc + bar = abcbar"

    result_tainted = mod.do_fstring(object_with_side_effects, b)
    assert result_tainted == result

    setattr(MagicMethodsException, "__format__", _old_method_format)


def test_encode_aspect_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    result = mod.do_encode(object_with_side_effects)
    assert result == b"abc"


def test_encode_aspect_side_effects_none():
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'encode'"):
        mod.do_encode(None)


def test_decode_aspect_side_effects():
    object_with_side_effects = MagicMethodsException(b"abc")

    result = mod.do_decode(object_with_side_effects)
    assert result == STRING_TO_TAINT


def test_decode_aspect_side_effects_none():
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'decode'"):
        mod.do_decode(None)


def test_replace_aspect_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)
    old_text = "b"
    new_text = "fgh"
    result = mod.do_replace(object_with_side_effects, old_text, new_text)
    assert result == "afghc"


def test_replace_aspect_side_effects_none():
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'replace'"):
        old_text = "b"
        new_text = "fgh"
        mod.do_replace(None, old_text, new_text)


@pytest.mark.parametrize(
    "method",
    (
        "upper",
        "lower",
        "swapcase",
        "title",
        "capitalize",
        "casefold",
    ),
)
def test_common_aspect_side_effects(method):
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    result = getattr(mod, f"do_{method}")(object_with_side_effects)
    assert result == STRING_TO_TAINT


@pytest.mark.parametrize(
    "method",
    (
        "upper",
        "lower",
        "swapcase",
        "title",
        "capitalize",
        "casefold",
    ),
)
def test_common_aspect_side_effects_none(method):
    with pytest.raises(AttributeError, match=f"'NoneType' object has no attribute '{method}'"):
        getattr(mod, f"do_{method}")(None)


def test_translate_aspect_side_effects():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    result = mod.do_translate(object_with_side_effects, {"a"})
    assert result == STRING_TO_TAINT


def test_translate_aspect_side_effects_none():
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'translate'"):
        mod.do_translate(None, {"a"})


def test_taint_pyobject_none():
    result = taint_pyobject(
        pyobject=None,
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    assert result is None


def test_taint_pyobject_with_ranges():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    result = taint_pyobject_with_ranges(pyobject=object_with_side_effects, ranges=tuple())
    assert result is False


def test_taint_pyobject_with_ranges_none():
    result = taint_pyobject_with_ranges(pyobject=None, ranges=tuple())
    assert result is False


def test_get_tainted_ranges():
    object_with_side_effects = MagicMethodsException(STRING_TO_TAINT)

    result = get_tainted_ranges(pyobject=object_with_side_effects)
    assert result == ()


def test_get_tainted_ranges_none():
    result = get_tainted_ranges(None)
    assert result == ()
