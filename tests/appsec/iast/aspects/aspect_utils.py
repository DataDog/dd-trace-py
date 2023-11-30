# -*- encoding: utf-8 -*-
import re
from typing import Any  # noqa:F401
from typing import List  # noqa:F401
from typing import NamedTuple  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Text  # noqa:F401

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject_with_ranges
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")

EscapeContext = NamedTuple("EscapeContext", [("id", Any), ("position", int)])

DEFAULT_PARAMETER_NAME = ""
TAINT_FORMAT_CAPTURE = r"\:\+-(?:\<(?P<inputid>[0-9a-zA-Z\-]+)\>)?(.+?)(?:\<(?P=inputid)\>)?-\+\:"
TAINT_FORMAT_PATTERN = re.compile(TAINT_FORMAT_CAPTURE, re.MULTILINE | re.DOTALL)
TAINT_FORMAT_CAPTURE_BYTES = rb"\:\+-(?:\<(?P<inputid>[0-9a-zA-Z\-]+)\>)?(.+?)(?:\<(?P=inputid)\>)?-\+\:"  # noqa: E501; pylint: disable=anomalous-backslash-in-string
TAINT_FORMAT_PATTERN_BYTES = re.compile(TAINT_FORMAT_CAPTURE_BYTES, re.MULTILINE | re.DOTALL)


def create_taint_range_with_format(text_input, fn_origin=""):  # type: (Any, str) -> Any
    is_bytes = isinstance(text_input, (bytes, bytearray))
    taint_format_capture = TAINT_FORMAT_CAPTURE_BYTES if is_bytes else TAINT_FORMAT_CAPTURE
    taint_format_pattern = TAINT_FORMAT_PATTERN_BYTES if is_bytes else TAINT_FORMAT_PATTERN

    text_output = re.sub(  # type: ignore
        taint_format_capture, r"\2", text_input, flags=re.MULTILINE | re.DOTALL
    )  # type: Any
    if isinstance(text_input, bytearray):
        text_output = bytearray(text_output)  # type: ignore

    ranges_ = []  # type: List[TaintRange]
    acc_input_id = 0
    for i, match in enumerate(taint_format_pattern.finditer(text_input)):  # type: ignore[attr-defined]
        match_start = match.start() - (i * 6) - acc_input_id
        match_end = match.end() - ((i + 1) * 6) - acc_input_id
        match_group_1 = match.group(1)

        if match_group_1:
            len_input_id_token = len(match_group_1) + 2
            acc_input_id += 2 * len_input_id_token
            match_end -= 2 * len_input_id_token

        ranges_.append(
            TaintRange(
                match_start,
                match_end - match_start,
                Source(
                    str(match_group_1) if match_group_1 else DEFAULT_PARAMETER_NAME,
                    "sample_value",
                    OriginType.PARAMETER,
                ),
            )
        )

    taint_pyobject_with_ranges(
        text_output,
        ranges_,
    )
    return text_output


class BaseReplacement(object):
    def _to_tainted_string_with_origin(self, text):
        # type: (Text) -> Text
        if not isinstance(text, (str, bytes, bytearray)):
            return text

        # CAVEAT: the sequences ":+-" and "-+:" can be escaped with  "::++--" and "--+*::"
        elements = re.split(r"(\:\+-<[0-9a-zA-Z\-]+>|<[0-9a-zA-Z\-]+>-\+\:)", text)

        ranges = []  # type: List[TaintRange]
        ranges_append = ranges.append
        new_text = text.__class__()
        context = None  # type: Optional[EscapeContext]
        for index, element in enumerate(elements):
            if index % 2 == 0:
                element = element.replace("::++--", ":+-")
                element = element.replace("--++::", "-+:")
                new_text += element
            else:
                separator = element
                if element.startswith(":"):
                    id_evidence = separator[4:-1]
                    start = len(new_text)
                    context = EscapeContext(id_evidence, start)
                else:
                    id_evidence = separator[1:-4]
                    end = len(new_text)
                    assert context is not None
                    start = context.position
                    if start != end:
                        assert isinstance(id_evidence, str)

                        ranges_append(
                            TaintRange(
                                start,
                                end - start,
                                Source(name=id_evidence, value=new_text[start:], origin=OriginType.PARAMETER),
                            )
                        )
        set_ranges(new_text, tuple(ranges))
        return new_text

    def _assert_format_result(
        self,
        taint_escaped_template,  # type: Text
        taint_escaped_parameter,  # type: Any
        expected_result,  # type: Text
        escaped_expected_result,  # type: Text
    ):  # type: (...) -> None
        template = self._to_tainted_string_with_origin(taint_escaped_template)
        parameter = self._to_tainted_string_with_origin(taint_escaped_parameter)
        result = mod.do_format_with_positional_parameter(template, parameter)

        assert result == expected_result
        assert as_formatted_evidence(result) == escaped_expected_result

        # TODO: Uncomment when kwargs work to check format with named arguments
        # template = self._to_tainted_string_with_origin(taint_escaped_template)
        # template = re.sub('\{([^\}]*)\}', '{key\\1}', template)
        # result = mod.do_format_with_named_parameter(template, parameter)

        assert result == expected_result
        assert as_formatted_evidence(result, tag_mapping_function=None) == escaped_expected_result
