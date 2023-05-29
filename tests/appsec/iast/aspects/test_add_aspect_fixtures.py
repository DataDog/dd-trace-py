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


class TestOperatorAddReplacement(object):
    def test_nostring_operator_add(self):
        # type: () -> None
        assert mod.do_operator_add_params(2, 3) == 5

    def test_regression_operator_add_re_compile(self):  # type: () -> None
        try:
            mod.do_add_re_compile()
        except Exception as e:
            pytest.fail(e)

    def test_string_operator_add_none_tainted(
        self,
    ):  # type: () -> None
        string_input = "foo"
        bar = "bar"
        result = mod.do_operator_add_params(string_input, bar)
        assert not get_tainted_ranges(result)

    def test_operator_add_dis(
        self,
    ):  # type: () -> None
        import dis

        bytecode = dis.Bytecode(mod.do_operator_add_params)
        dis.dis(mod.do_operator_add_params)
        assert bytecode.codeobj.co_names == ("ddtrace_aspects", "add_aspect")

    def test_string_operator_add_one_tainted(self):  # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)
        string_input = taint_pyobject("foo", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER))
        bar = "bar"
        assert get_tainted_ranges(string_input)
        result = mod.do_operator_add_params(string_input, bar)
        assert len(get_tainted_ranges(result)) == 1

    def test_string_operator_add_two(self):  # type: () -> None
        string_input = taint_pyobject("foo", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER))
        bar = taint_pyobject("bar", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER))

        result = mod.do_operator_add_params(string_input, bar)
        assert len(get_tainted_ranges(result)) == 2

    def test_decoration_when_function_and_decorator_modify_texts_then_tainted(
        self,
    ):  # type: () -> None

        prefix = taint_pyobject("a", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER))
        suffix = taint_pyobject("b", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER))

        result = mod.do_add_and_uppercase(prefix, suffix)

        assert result == "AB"
        # TODO: migrate aspect title
        # assert len(get_tainted_ranges(result)) == 2

    def test_string_operator_add_one_tainted_mixed_bytearray_bytes(self):  # type: () -> None
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)
        string_input = taint_pyobject(b"foo", Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER))
        bar = bytearray("bar", encoding="utf-8")
        result = mod.do_operator_add_params(string_input, bar)
        assert result == b"foobar"
        # TODO: error
        #     def add_aspect(op1, op2):
        #         if not isinstance(op1, TEXT_TYPES) or not isinstance(op2, TEXT_TYPES):
        #             return op1 + op2
        # >       return _add_aspect(op1, op2)
        # E       SystemError: <method 'join' of 'bytes' objects> returned a result with an exception set
        assert len(get_tainted_ranges(result)) == 2

    def test_string_operator_add_two_mixed_bytearray_bytes(self):  # type: () -> None
        string_input = taint_pyobject(
            bytearray("foo", encoding="utf-8"),
            Source("test_add_aspect_tainting_left_hand", "foo", OriginType.PARAMETER),
        )
        bar = taint_pyobject(b"bar", Source("test_add_aspect_tainting_left_hand", "bar", OriginType.PARAMETER))

        result = mod.do_operator_add_params(string_input, bar)
        assert result == bytearray(b"foobar")
        assert len(get_tainted_ranges(result)) == 2
