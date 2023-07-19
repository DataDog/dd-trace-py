from builtins import bytearray as builtin_bytearray
from builtins import bytes as builtin_bytes
from builtins import str as builtin_str
import codecs
import sys
from typing import TYPE_CHECKING

from ddtrace.appsec.iast._taint_tracking import OriginType
from ddtrace.appsec.iast._taint_tracking import TagMappingMode
from ddtrace.appsec.iast._taint_tracking import TaintRange
from ddtrace.appsec.iast._taint_tracking import _convert_escaped_text_to_tainted_text
from ddtrace.appsec.iast._taint_tracking import are_all_text_all_ranges
from ddtrace.appsec.iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec.iast._taint_tracking import common_replace
from ddtrace.appsec.iast._taint_tracking import get_ranges
from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec.iast._taint_tracking import parse_params
from ddtrace.appsec.iast._taint_tracking import shift_taint_range
from ddtrace.appsec.iast._taint_tracking import taint_pyobject
from ddtrace.appsec.iast._taint_tracking import taint_pyobject_with_ranges
from ddtrace.appsec.iast._taint_tracking._native import aspects  # noqa: F401
from ddtrace.internal.compat import iteritems


if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Union

TEXT_TYPES = (str, bytes, bytearray)

_add_aspect = aspects.add_aspect
_extend_aspect = aspects.extend_aspect
_join_aspect = aspects.join_aspect


__all__ = ["add_aspect", "str_aspect", "bytearray_extend_aspect", "decode_aspect", "encode_aspect"]


def add_aspect(op1, op2):
    if not isinstance(op1, TEXT_TYPES) or not isinstance(op2, TEXT_TYPES) or type(op1) != type(op2):
        return op1 + op2
    return _add_aspect(op1, op2)


def str_aspect(*args, **kwargs):
    # type: (Any, Any) -> str
    # TODO: migrate this function to C++ and shift ranges instead to create a new one
    result = builtin_str(*args, **kwargs)
    if isinstance(args[0], TEXT_TYPES) and is_pyobject_tainted(args[0]):
        result = taint_pyobject(
            pyobject=result, source_name="str_aspect", source_value=result, source_origin=OriginType.PARAMETER
        )

    return result


def join_aspect(joiner, *args, **kwargs):
    # type: (Any, Any, Any) -> Any
    if not isinstance(joiner, TEXT_TYPES):
        return joiner.join(*args, **kwargs)
    return _join_aspect(joiner, *args, **kwargs)


def bytearray_extend_aspect(op1, op2):
    # type: (Any, Any) -> Any
    if not isinstance(op1, bytearray) or not isinstance(op2, (bytearray, bytes)):
        return op1.extend(op2)
    return _extend_aspect(op1, op2)


def bytes_aspect(*args, **kwargs):
    # type: (Any, Any) -> bytes
    result = builtin_bytes(*args, **kwargs)
    if isinstance(args[0], TEXT_TYPES) and is_pyobject_tainted(args[0]):
        result = taint_pyobject(result, source_name="bytes_aspect", source_value=result)

    return result


def bytearray_aspect(*args, **kwargs):
    # type: (Any, Any) -> bytearray
    result = builtin_bytearray(*args, **kwargs)
    if isinstance(args[0], TEXT_TYPES) and is_pyobject_tainted(args[0]):
        result = taint_pyobject(result, source_name="bytearray_aspect", source_value=result)

    return result


def modulo_aspect(candidate_text, candidate_tuple):
    # type: (Any, Any) -> Any
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text % candidate_tuple

    try:
        if isinstance(candidate_tuple, tuple):
            parameter_list = candidate_tuple
        else:
            parameter_list = (candidate_tuple,)

        ranges_orig, candidate_text_ranges = are_all_text_all_ranges(candidate_text, parameter_list)
        if not ranges_orig:
            return candidate_text % candidate_tuple

        return _convert_escaped_text_to_tainted_text(
            as_formatted_evidence(
                candidate_text,
                candidate_text_ranges,
                tag_mapping_function=TagMappingMode.Mapper,
            )
            % tuple(
                as_formatted_evidence(
                    parameter,
                    tag_mapping_function=TagMappingMode.Mapper,
                )
                if isinstance(parameter, TEXT_TYPES)
                else parameter
                for parameter in parameter_list
            ),
            ranges_orig=ranges_orig,
        )
    except Exception:
        return candidate_text % candidate_tuple


def build_string_aspect(*args):  # type: (List[Any]) -> str
    return join_aspect("", args)


def ljust_aspect(candidate_text, *args, **kwargs):
    # type: (Any, Any, Any) -> Union[str, bytes, bytearray]
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.ljust(*args, **kwargs)

    ranges_new = get_ranges(candidate_text)
    fillchar = parse_params(1, "fillchar", " ", *args, **kwargs)
    fillchar_ranges = get_ranges(fillchar)
    if ranges_new is None or (not ranges_new and not fillchar_ranges):
        return candidate_text.ljust(*args, **kwargs)

    if fillchar_ranges:
        # Can only be one char, so we create one range to cover from the start to the end
        ranges_new = ranges_new + [shift_taint_range(fillchar_ranges[0], len(candidate_text))]

    res = candidate_text.ljust(parse_params(0, "width", None, *args, **kwargs), fillchar)
    taint_pyobject_with_ranges(res, ranges_new)
    return res


def zfill_aspect(candidate_text, *args, **kwargs):
    # type: (Any, Any, Any) -> str
    return candidate_text.zfill(*args, **kwargs)


def format_aspect(
    candidate_text,  # type: str
    *args,  # type: List[Any]
    **kwargs  # type: Dict[str, Any]
):  # type: (...) -> str
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.format(*args, **kwargs)

    params = tuple(args) + tuple(kwargs.values())
    ranges_orig, candidate_text_ranges = are_all_text_all_ranges(candidate_text, params)
    if not ranges_orig:
        return candidate_text.format(*args, **kwargs)

    new_template = as_formatted_evidence(
        candidate_text, candidate_text_ranges, tag_mapping_function=TagMappingMode.Mapper
    )
    fun = (
        lambda arg: as_formatted_evidence(arg, tag_mapping_function=TagMappingMode.Mapper)
        if isinstance(arg, TEXT_TYPES)
        else arg
    )

    new_args = list(map(fun, args))
    new_kwargs = {key: fun(value) for key, value in iteritems(kwargs)}

    return _convert_escaped_text_to_tainted_text(
        new_template.format(*new_args, **new_kwargs),
        ranges_orig=ranges_orig,
    )


def format_map_aspect(candidate_text, *args, **kwargs):  # type: (str, Any, Any) -> str
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.format_map(*args, **kwargs)

    mapping = parse_params(0, "mapping", None, *args, **kwargs)
    mapping_tuple = tuple(mapping if not isinstance(mapping, dict) else mapping.values())
    ranges_orig, candidate_text_ranges = are_all_text_all_ranges(
        candidate_text,
        args + mapping_tuple,
    )
    if not ranges_orig:
        return candidate_text.format_map(*args, **kwargs)

    return _convert_escaped_text_to_tainted_text(
        as_formatted_evidence(
            candidate_text, candidate_text_ranges, tag_mapping_function=TagMappingMode.Mapper
        ).format_map(
            {
                key: as_formatted_evidence(value, tag_mapping_function=TagMappingMode.Mapper)
                if isinstance(value, TEXT_TYPES)
                else value
                for key, value in iteritems(mapping)
            }
        ),
        ranges_orig=ranges_orig,
    )


def format_value_aspect(
    element,  # type: Any
    options=0,  # type: int
    format_spec=None,  # type: Optional[str]
):  # type: (...) -> str

    if options == 115:
        new_text = str(element)
    elif options == 114:
        # TODO: use our repr once we have implemented it
        new_text = repr(element)
    elif options == 97:
        # TODO: use our ascii once we have implemented it
        if sys.version_info[0] >= 3:
            new_text = ascii(element)
    else:
        new_text = element

    if format_spec:
        # Apply formatting
        new_text = format_aspect("{:%s}" % format_spec, new_text)  # type:ignore
    else:
        new_text = str(new_text)

    # FIXME: can we return earlier here?
    if not isinstance(new_text, TEXT_TYPES):
        return new_text

    ranges_new = get_tainted_ranges(new_text) if isinstance(element, TEXT_TYPES) else ()
    if not ranges_new:
        return new_text

    taint_pyobject_with_ranges(new_text, ranges_new)
    return new_text


def incremental_translation(self, incr_coder, funcode, empty):
    tainted_ranges = iter(get_tainted_ranges(self))
    result_list, new_ranges = [], []
    result_length, i = 0, 0
    tainted_range = next(tainted_ranges, None)
    try:
        for i in range(len(self)):
            if tainted_range is None:
                # no more tainted ranges, finish decoding all at once
                new_prod = funcode(self[i:])
                result_list.append(new_prod)
                break
            if i == tainted_range.start:
                # start new tainted range
                new_ranges.append(TaintRange(start=i, length=result_length, source=tainted_range.source))

            new_prod = funcode(self[i : i + 1])
            result_list.append(new_prod)
            result_length += len(new_prod)

            if i + 1 == tainted_range.start + tainted_range.length:
                # end range. Do no taint partial multi-bytes character that comes next.
                # new_ranges[-1].append(result_length - new_ranges[-1].length)
                new_ranges[-1] = TaintRange(
                    start=new_ranges[-1].length,
                    length=(result_length - new_ranges[-1].length),
                    source=new_ranges[-1].source,
                )
                tainted_range = next(tainted_ranges, None)
        result_list.append(funcode(self[:0], True))
    except UnicodeDecodeError as e:
        offset = -len(incr_coder.getstate()[0])
        raise UnicodeDecodeError(e.args[0], self, i + e.args[2] + offset, i + e.args[3] + offset, *e.args[4:])
    except UnicodeEncodeError:
        funcode(self)
    result = empty.join(result_list)
    taint_pyobject_with_ranges(result, new_ranges)
    return result


def decode_aspect(self, *args, **kwargs):
    if not is_pyobject_tainted(self) or not isinstance(self, bytes):
        return self.decode(*args, **kwargs)
    codec = args[0] if args else "utf-8"
    inc_dec = codecs.getincrementaldecoder(codec)(**kwargs)
    return incremental_translation(self, inc_dec, inc_dec.decode, "")


def encode_aspect(self, *args, **kwargs):
    if not is_pyobject_tainted(self) or not isinstance(self, str):
        return self.encode(*args, **kwargs)
    codec = args[0] if args else "utf-8"
    inc_enc = codecs.getincrementalencoder(codec)(**kwargs)
    return incremental_translation(self, inc_enc, inc_enc.encode, b"")


def upper_aspect(candidate_text, *args, **kwargs):  # type: (Any, Any, Any) -> str
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.upper(*args, **kwargs)

    return common_replace("upper", candidate_text, *args, **kwargs)


def lower_aspect(candidate_text, *args, **kwargs):  # type: (Any, Any, Any) -> str
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.lower(*args, **kwargs)

    return common_replace("lower", candidate_text, *args, **kwargs)


def swapcase_aspect(candidate_text, *args, **kwargs):  # type: (Any, Any, Any) -> str
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.swapcase(*args, **kwargs)

    return common_replace("swapcase", candidate_text, *args, **kwargs)


def title_aspect(candidate_text, *args, **kwargs):  # type: (Any, Any, Any) -> str
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.title(*args, **kwargs)

    return common_replace("title", candidate_text, *args, **kwargs)


def capitalize_aspect(candidate_text, *args, **kwargs):  # type: (Any, Any, Any) -> str
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.capitalize(*args, **kwargs)

    return common_replace("capitalize", candidate_text, *args, **kwargs)


def casefold_aspect(candidate_text, *args, **kwargs):  # type: (Any, Any, Any) -> str
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.casefold(*args, **kwargs)
    return common_replace("casefold", candidate_text, *args, **kwargs)


def translate_aspect(candidate_text, *args, **kwargs):  # type: (Any, Any, Any) -> str
    if not isinstance(candidate_text, TEXT_TYPES):
        return candidate_text.translate(*args, **kwargs)
    return common_replace("translate", candidate_text, *args, **kwargs)
