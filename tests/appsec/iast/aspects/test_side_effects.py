import pytest

import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast.iast_utils_side_effects import MagicMethodsException


mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")


def test_radd_aspect_side_effects():
    string_to_taint = "abc"
    object_with_side_effects = MagicMethodsException(string_to_taint)

    def __radd__(self, a):
        print(self)
        print(a)
        return self._data + a

    _old_method = getattr(MagicMethodsException, "__radd__", None)
    setattr(MagicMethodsException, "__radd__", __radd__)
    result = "123" + object_with_side_effects
    assert result == string_to_taint + "123"

    result_tainted = ddtrace_aspects.add_aspect("123", object_with_side_effects)

    assert result_tainted == result

    setattr(MagicMethodsException, "__radd__", _old_method)


def test_add_aspect_side_effects():
    string_to_taint = "abc"
    object_with_side_effects = MagicMethodsException(string_to_taint)

    def __add__(self, a):
        return self._data + a

    _old_method = getattr(MagicMethodsException, "__add__", None)
    setattr(MagicMethodsException, "__add__", __add__)
    result = object_with_side_effects + "123"
    assert result == "abc123"

    result_tainted = ddtrace_aspects.add_aspect(object_with_side_effects, "123")

    assert result_tainted == result

    setattr(MagicMethodsException, "__radd__", _old_method)


def test_join_aspect_side_effects():
    string_to_taint = "abc"
    object_with_side_effects = MagicMethodsException(string_to_taint)

    it = ["a", "b", "c"]

    result = mod.do_join(object_with_side_effects, it)
    assert result == "abcabc"


def test_encode_aspect_side_effects():
    string_to_taint = "abc"
    object_with_side_effects = MagicMethodsException(string_to_taint)

    result = mod.do_encode(object_with_side_effects)
    assert result == b"abc"


def test_encode_aspect_side_effects_none():
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'encode'"):
        mod.do_encode(None)
