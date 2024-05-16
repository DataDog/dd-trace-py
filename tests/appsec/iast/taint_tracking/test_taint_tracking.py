#!/usr/bin/env python3
import logging

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from tests.utils import override_env


with override_env({"DD_IAST_ENABLED": "True"}):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import Source
    from ddtrace.appsec._iast._taint_tracking import TaintRange
    from ddtrace.appsec._iast._taint_tracking import num_objects_tainted
    from ddtrace.appsec._iast._taint_tracking import reset_context
    from ddtrace.appsec._iast._taint_tracking import set_ranges
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject
    from ddtrace.appsec._iast._taint_tracking import taint_ranges_as_evidence_info
    from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect


def setup():
    oce._enabled = True


def test_taint_ranges_as_evidence_info_nothing_tainted():
    text = "nothing tainted"
    value_parts, sources = taint_ranges_as_evidence_info(text)
    assert value_parts == [{"value": text}]
    assert sources == []


def test_taint_ranges_as_evidence_info_all_tainted():
    arg = "all tainted"
    input_info = Source("request_body", arg, OriginType.PARAMETER)
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    value_parts, sources = taint_ranges_as_evidence_info(tainted_text)
    assert value_parts == [{"value": tainted_text, "source": 0}]
    assert sources == [input_info]


@pytest.mark.skip_iast_check_logs
def test_taint_object_with_no_context_should_be_noop():
    reset_context()
    arg = "all tainted"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    assert tainted_text == arg
    assert num_objects_tainted() == 0


@pytest.mark.skip_iast_check_logs
def test_propagate_ranges_with_no_context(caplog):
    reset_context()
    with override_env({IAST.ENV_DEBUG: "true"}), caplog.at_level(logging.DEBUG):
        string_input = taint_pyobject(
            pyobject="abcde", source_name="abcde", source_value="abcde", source_origin=OriginType.PARAMETER
        )
        assert string_input == "abcde"
    log_messages = [record.message for record in caplog.get_records("call")]
    assert any("[IAST] " in message for message in log_messages), log_messages


@pytest.mark.skip_iast_check_logs
def test_call_to_set_ranges_directly_raises_a_exception(caplog):
    reset_context()
    input_str = "abcde"
    with pytest.raises(ValueError) as excinfo:
        set_ranges(
            input_str,
            [TaintRange(0, len(input_str), Source(input_str, "sample_value", OriginType.PARAMETER))],
        )
    assert str(excinfo.value).startswith("[IAST] Tainted Map isn't initialized")


def test_taint_ranges_as_evidence_info_tainted_op1_add():
    arg = "tainted part"
    input_info = Source("request_body", arg, OriginType.PARAMETER)
    text = "|not tainted part|"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    tainted_add_result = add_aspect(tainted_text, text)

    value_parts, sources = taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [{"value": tainted_text, "source": 0}, {"value": text}]
    assert sources == [input_info]


def test_taint_ranges_as_evidence_info_tainted_op2_add():
    arg = "tainted part"
    input_info = Source("request_body", arg, OriginType.PARAMETER)
    text = "|not tainted part|"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    tainted_add_result = add_aspect(text, tainted_text)

    value_parts, sources = taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [{"value": text}, {"value": tainted_text, "source": 0}]
    assert sources == [input_info]


def test_taint_ranges_as_evidence_info_same_tainted_op1_and_op3_add():
    arg = "tainted part"
    input_info = Source("request_body", arg, OriginType.PARAMETER)
    text = "|not tainted part|"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    tainted_add_result = add_aspect(tainted_text, add_aspect(text, tainted_text))

    value_parts, sources = taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [{"value": tainted_text, "source": 0}, {"value": text}, {"value": tainted_text, "source": 0}]
    assert sources == [input_info]


def test_taint_ranges_as_evidence_info_different_tainted_op1_and_op3_add():
    arg1 = "tainted body"
    arg2 = "tainted header"
    input_info1 = Source("request_body", arg1, OriginType.PARAMETER)
    input_info2 = Source("request_body", arg2, OriginType.PARAMETER)
    text = "|not tainted part|"
    tainted_text1 = taint_pyobject(
        arg1, source_name="request_body", source_value=arg1, source_origin=OriginType.PARAMETER
    )
    tainted_text2 = taint_pyobject(
        arg2, source_name="request_body", source_value=arg2, source_origin=OriginType.PARAMETER
    )
    tainted_add_result = add_aspect(tainted_text1, add_aspect(text, tainted_text2))

    value_parts, sources = taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [
        {"value": tainted_text1, "source": 0},
        {"value": text},
        {"value": tainted_text2, "source": 1},
    ]
    assert sources == [input_info1, input_info2]
