#!/usr/bin/env python3
import logging

import pytest

from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Source
from tests.utils import override_env
from tests.utils import override_global_config


with override_env({"DD_IAST_ENABLED": "True"}):
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import TaintRange
    from ddtrace.appsec._iast._taint_tracking import num_objects_tainted
    from ddtrace.appsec._iast._taint_tracking import set_ranges
    from ddtrace.appsec._iast._taint_tracking._context import reset_context
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
    from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect


def test_taint_ranges_as_evidence_info_nothing_tainted():
    text = "nothing tainted"
    sources, value_parts = IastSpanReporter.taint_ranges_as_evidence_info(text)
    assert value_parts == []
    assert sources == []


def test_taint_ranges_as_evidence_info_all_tainted():
    arg = "all tainted"
    input_info = Source(origin=OriginType.PARAMETER, name="request_body", value=arg)
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    sources, value_parts = IastSpanReporter.taint_ranges_as_evidence_info(tainted_text)
    assert value_parts == [{"start": 0, "end": 11, "length": 11, "source": input_info}]
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
    with override_env({"_DD_IAST_USE_ROOT_SPAN": "false"}), override_global_config(
        dict(_iast_debug=True)
    ), caplog.at_level(logging.DEBUG):
        string_input = taint_pyobject(
            pyobject="abcde", source_name="abcde", source_value="abcde", source_origin=OriginType.PARAMETER
        )
        assert string_input == "abcde"
    log_messages = [record.message for record in caplog.get_records("call")]
    assert any("iast::" in message for message in log_messages), log_messages


@pytest.mark.skip_iast_check_logs
def test_call_to_set_ranges_directly_raises_a_exception(caplog):
    from ddtrace.appsec._iast._taint_tracking import Source as TaintRangeSource

    reset_context()
    input_str = "abcde"
    with pytest.raises(ValueError) as excinfo:
        set_ranges(
            input_str,
            [TaintRange(0, len(input_str), TaintRangeSource(input_str, "sample_value", OriginType.PARAMETER), [])],
        )
    assert str(excinfo.value).startswith("iast::propagation::native::error::Tainted Map isn't initialized")


def test_taint_ranges_as_evidence_info_tainted_op1_add():
    arg = "tainted part"
    input_info = Source(origin=OriginType.PARAMETER, name="request_body", value=arg)
    text = "|not tainted part|"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    tainted_add_result = add_aspect(tainted_text, text)

    sources, value_parts = IastSpanReporter.taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [{"start": 0, "end": 12, "length": 12, "source": input_info}]
    assert sources == [input_info]


def test_taint_ranges_as_evidence_info_tainted_op2_add():
    arg = "tainted part"
    input_info = Source(origin=OriginType.PARAMETER, name="request_body", value=arg)
    text = "|not tainted part|"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    tainted_add_result = add_aspect(text, tainted_text)

    sources, value_parts = IastSpanReporter.taint_ranges_as_evidence_info(tainted_add_result)
    assert (
        value_parts
        == [{"end": 30, "length": 12, "source": input_info, "start": 18}]
        != [{"end": 12, "length": 12, "source": input_info, "start": 0}]
    )
    assert sources == [input_info]


def test_taint_ranges_as_evidence_info_same_tainted_op1_and_op3_add():
    arg = "tainted part"
    input_info = Source(origin=OriginType.PARAMETER, name="request_body", value=arg)
    text = "|not tainted part|"
    tainted_text = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    tainted_add_result = add_aspect(tainted_text, add_aspect(text, tainted_text))

    (
        sources,
        value_parts,
    ) = IastSpanReporter.taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [
        {"end": 12, "length": 12, "source": input_info, "start": 0},
        {"end": 42, "length": 12, "source": input_info, "start": 30},
    ]

    assert sources == [input_info]


def test_taint_ranges_as_evidence_info_different_tainted_op1_and_op3_add():
    arg1 = "tainted body"
    arg2 = "tainted header"
    input_info1 = Source(origin=OriginType.PARAMETER, name="request_body", value=arg1)
    input_info2 = Source(origin=OriginType.PARAMETER, name="request_body", value=arg2)
    text = "|not tainted part|"
    tainted_text1 = taint_pyobject(
        arg1, source_name="request_body", source_value=arg1, source_origin=OriginType.PARAMETER
    )
    tainted_text2 = taint_pyobject(
        arg2, source_name="request_body", source_value=arg2, source_origin=OriginType.PARAMETER
    )
    tainted_add_result = add_aspect(tainted_text1, add_aspect(text, tainted_text2))

    sources, value_parts = IastSpanReporter.taint_ranges_as_evidence_info(tainted_add_result)
    assert value_parts == [
        {"end": 12, "length": 12, "source": input_info1, "start": 0},
        {"end": 44, "length": 14, "source": input_info2, "start": 30},
    ]
    assert sources == [input_info1, input_info2]
