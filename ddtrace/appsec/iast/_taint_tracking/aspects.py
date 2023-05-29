#!/usr/bin/env python3

from builtins import str as builtin_str
from builtins import bytes as builtin_bytes
from builtins import bytearray as builtin_bytearray
import codecs
from typing import TYPE_CHECKING
from typing import Text

from six import StringIO
from six import binary_type

from ddtrace.appsec.iast._source import _Source
from ddtrace.appsec.iast._taint_tracking import add_taint_pyobject
from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec.iast._taint_tracking import set_tainted_ranges
from ddtrace.appsec.iast._taint_tracking import taint_pyobject

from ddtrace.appsec.iast._taint_tracking._native import aspects  # noqa: F401
if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Optional

TEXT_TYPES = (Text, binary_type, bytearray)

_add_aspect = aspects.add_aspect



__all__ = ["add_aspect", "str_aspect", "decode_aspect", "encode_aspect"]


def add_aspect(op1, op2):
    if not isinstance(op1, TEXT_TYPES) or not isinstance(op2, TEXT_TYPES):
        return op1 + op2
    return _add_aspect(op1, op2)


def str_aspect(*args, **kwargs):
    # type: (Any, Any) -> str
    result = builtin_str(*args, **kwargs)
    if isinstance(args[0], TEXT_TYPES) and is_pyobject_tainted(args[0]):
        result = taint_pyobject(result, _Source("str_aspect", result, 0))

    return result


def bytes_aspect(*args, **kwargs):
    # type: (Any, Any) -> bytes
    result = builtin_bytes(*args, **kwargs)
    if isinstance(args[0], (str, bytes, bytearray)) and is_pyobject_tainted(args[0]):
        result = taint_pyobject(result, _Source("bytes_aspect", result, 0))

    return result


def bytearray_aspect(*args, **kwargs):
    # type: (Any, Any) -> str
    result = builtin_bytearray(*args, **kwargs)
    if isinstance(args[0], (str, bytes, bytearray)) and is_pyobject_tainted(args[0]):
        result = taint_pyobject(result, _Source("bytearray_aspect", result, 0))

    return result


def stringio_aspect(*args, **kwargs):
    # type: (Any, Any) -> StringIO
    return StringIO(*args, **kwargs)


def modulo_aspect(candidate_text, candidate_tuple):
    # type: (Any, Any) -> Any
    if not get_propagation():
        return candidate_text % candidate_tuple

    try:
        if isinstance(candidate_tuple, tuple):
            parameter_list = candidate_tuple
        else:
            parameter_list = (candidate_tuple,)  # type: ignore

        ranges_orig, candidate_text_ranges = are_all_text_all_ranges(
            candidate_text, parameter_list
        )
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
    except Exception as exc:
        return candidate_text % candidate_tuple


def format_aspect(
    candidate_text,  # type: str
    *args,  # type: List[Any]
    **kwargs  # type: Dict[str, Any]
):  # type: (...) -> str
    if not get_propagation():
        return candidate_text.format(*args, **kwargs)

    try:
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
        new_args = map(fun, args)  # type: ignore[arg-type]
        new_kwargs = {key: fun(value) for key, value in iteritems(kwargs)}
        # invert_dict(range_guid_map)
        return _convert_escaped_text_to_tainted_text(
            new_template.format(*new_args, **new_kwargs),  # type: ignore[union-attr]
            ranges_orig=ranges_orig,
        )

    except Exception as exc:
        return candidate_text.format(*args, **kwargs)


def format_map_aspect(candidate_text, *args, **kwargs):  # type: (str, Any, Any) -> str
    if not get_propagation():
        return candidate_text.format_map(*args, **kwargs)

    try:
        mapping = parse_params(0, 'mapping', None, *args, **kwargs)
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

    except Exception as exc:
        return candidate_text.format_map(*args, **kwargs)


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
        new_text = aspect_format('{:%s}' % format_spec, new_text)  # type:ignore
    else:
        new_text = str(new_text)

    # FIXME: can we return earlier here?
    if not get_propagation():
        return new_text

    ranges_new = get_ranges(new_text) if isinstance(element, TEXT_TYPES) else ()
    if not ranges_new:
        return new_text

    new_text_new_id = copy_string_new_id(new_text)
    set_ranges(new_text_new_id, ranges_new)
    return new_text_new_id


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
            if i == tainted_range[1]:
                # start new tainted range
                new_ranges.append([tainted_range[0], result_length])
            new_prod = funcode(self[i : i + 1])
            result_list.append(new_prod)
            result_length += len(new_prod)
            if i + 1 == tainted_range[1] + tainted_range[2]:
                # end range. Do no taint partial multi-bytes character that comes next.
                new_ranges[-1].append(result_length - new_ranges[-1][-1])
                tainted_range = next(tainted_ranges, None)
        result_list.append(funcode(self[:0], True))
    except UnicodeDecodeError as e:
        offset = -len(incr_coder.getstate()[0])
        raise UnicodeDecodeError(e.args[0], self, i + e.args[2] + offset, i + e.args[3] + offset, *e.args[4:])
    except UnicodeEncodeError:
        funcode(self)
    result = empty.join(result_list)
    set_tainted_ranges(result, new_ranges)
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
