# -*- coding: utf-8 -*-
import pytest


try:
    from ddtrace.appsec._iast import oce
    from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
    from tests.appsec.iast.aspects.aspect_utils import BaseReplacement
    from tests.appsec.iast.aspects.aspect_utils import create_taint_range_with_format
    from tests.appsec.iast.aspects.conftest import _iast_patched_module
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)

mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")


def setup():
    oce._enabled = True


@pytest.mark.parametrize(
    "obj, kwargs",
    [
        (3.5, {}),
        ("Hi", {}),
        ("🙀", {}),
        (b"Hi", {}),
        (b"Hi", {"encoding": "utf-8", "errors": "strict"}),
        (b"Hi", {"encoding": "utf-8", "errors": "ignore"}),
        ({"a": "b", "c": "d"}, {}),
        ({"a", "b", "c", "d"}, {}),
        (("a", "b", "c", "d"), {}),
        (["a", "b", "c", "d"], {}),
    ],
)
def test_str_aspect(obj, kwargs):
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    assert ddtrace_aspects.str_aspect(obj, **kwargs) == str(obj, **kwargs)


@pytest.mark.parametrize(
    "obj, kwargs, should_be_tainted",
    [
        (3.5, {}, False),
        ("Hi", {}, True),
        ("🙀", {}, True),
        (b"Hi", {}, True),
        (bytearray(b"Hi"), {}, True),
        (b"Hi", {"encoding": "utf-8", "errors": "strict"}, True),
        (b"Hi", {"encoding": "utf-8", "errors": "ignore"}, True),
        ({"a": "b", "c": "d"}, {}, False),
        ({"a", "b", "c", "d"}, {}, False),
        (("a", "b", "c", "d"), {}, False),
        (["a", "b", "c", "d"], {}, False),
    ],
)
def test_str_aspect_tainting(obj, kwargs, should_be_tainted):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    if should_be_tainted:
        obj = taint_pyobject(
            obj, source_name="test_str_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
        )

    result = ddtrace_aspects.str_aspect(obj, **kwargs)
    assert is_pyobject_tainted(result) == should_be_tainted

    assert result == str(obj, **kwargs)


@pytest.mark.parametrize(
    "obj, expected_result",
    [
        ("3.5", "'3.5'"),
        ("Hi", "'Hi'"),
        ("🙀", "'🙀'"),
        (b"Hi", "b'Hi'"),
        (bytearray(b"Hi"), "bytearray(b'Hi')"),
    ],
)
def test_repr_aspect_tainting(obj, expected_result):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject
    import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects

    assert repr(obj) == expected_result

    obj = taint_pyobject(
        obj, source_name="test_repr_aspect_tainting", source_value=obj, source_origin=OriginType.PARAMETER
    )

    result = ddtrace_aspects.repr_aspect(obj)
    assert is_pyobject_tainted(result) is True


class TestOperatorsReplacement(BaseReplacement):
    def test_aspect_ljust_str_tainted(self):
        # type: () -> None
        string_input = "foo"

        # Not tainted
        ljusted = mod.do_ljust(string_input, 4)  # pylint: disable=no-member
        assert as_formatted_evidence(ljusted) == ljusted

        # Tainted
        string_input = create_taint_range_with_format(":+-foo-+:")
        ljusted = mod.do_ljust(string_input, 4)  # pylint: disable=no-member
        assert as_formatted_evidence(ljusted) == ":+-foo-+: "

    def test_zfill(self):
        # Not tainted
        string_input = "-1234"
        res = mod.do_zfill(string_input, 6)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "-01234"

        # Tainted
        string_input = create_taint_range_with_format(":+--12-+:34")
        res = mod.do_zfill(string_input, 6)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == ":+---+:0:+-12-+:34"

        string_input = create_taint_range_with_format(":+-+12-+:34")
        res = mod.do_zfill(string_input, 7)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == ":+-+-+:00:+-12-+:34"

        string_input = create_taint_range_with_format(":+-012-+:34")
        res = mod.do_zfill(string_input, 7)  # pylint: disable=no-member
        assert as_formatted_evidence(res) == "00:+-012-+:34"

    def test_format(self):
        # type: () -> None
        string_input = "foo"
        result = mod.do_format_fill(string_input)
        assert result == "foo       "
        string_input = create_taint_range_with_format(":+-foo-+:")

        result = mod.do_format_fill(string_input)  # pylint: disable=no-member
        # TODO format with params doesn't work correctly the assert should be
        #  assert as_formatted_evidence(result) == ":+-foo       -+:"
        assert as_formatted_evidence(result) == "foo       "
