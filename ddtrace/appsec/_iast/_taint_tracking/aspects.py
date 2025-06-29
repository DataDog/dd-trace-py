import _io
from builtins import bytearray as builtin_bytearray
from builtins import bytes as builtin_bytes
import codecs
import itertools
import os
from re import Match
from re import Pattern
from types import BuiltinFunctionType
from types import ModuleType
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Text
from typing import Tuple
from typing import Union

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._logs import iast_propagation_error_log
from ddtrace.appsec._iast._taint_tracking import TagMappingMode
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathbasename
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathdirname
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathjoin
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathnormcase
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathsplit
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathsplitdrive
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathsplitext
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathsplitroot
from ddtrace.appsec._iast._taint_tracking import _aspect_rsplit
from ddtrace.appsec._iast._taint_tracking import _aspect_split
from ddtrace.appsec._iast._taint_tracking import _aspect_splitlines
from ddtrace.appsec._iast._taint_tracking import _convert_escaped_text_to_tainted_text
from ddtrace.appsec._iast._taint_tracking import _format_aspect
from ddtrace.appsec._iast._taint_tracking import are_all_text_all_ranges
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import common_replace
from ddtrace.appsec._iast._taint_tracking import copy_and_shift_ranges_from_strings
from ddtrace.appsec._iast._taint_tracking import copy_ranges_from_strings
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking import new_pyobject_id
from ddtrace.appsec._iast._taint_tracking import parse_params
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking import shift_taint_range
from ddtrace.appsec._iast._taint_tracking._native import aspects  # noqa: F401
from ddtrace.appsec._iast._taint_tracking._taint_objects import copy_ranges_to_iterable_with_strings
from ddtrace.appsec._iast._taint_tracking._taint_objects import copy_ranges_to_string
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import taint_pyobject_with_ranges


TEXT_TYPES = Union[str, bytes, bytearray]

_extend_aspect = aspects.extend_aspect
_join_aspect = aspects.join_aspect
add_aspect = aspects.add_aspect
add_inplace_aspect = aspects.add_inplace_aspect
index_aspect = aspects.index_aspect
modulo_aspect = aspects.modulo_aspect
rsplit_aspect = _aspect_rsplit
slice_aspect = aspects.slice_aspect
split_aspect = _aspect_split
splitlines_aspect = _aspect_splitlines
str_aspect = aspects.str_aspect

__all__ = [
    "add_aspect",
    "add_inplace_aspect",
    "bytearray_aspect",
    "bytearray_extend_aspect",
    "bytes_aspect",
    "bytesio_aspect",
    "capitalize_aspect",
    "casefold_aspect",
    "decode_aspect",
    "encode_aspect",
    "format_aspect",
    "format_map_aspect",
    "index_aspect",
    "join_aspect",
    "ljust_aspect",
    "lower_aspect",
    "modulo_aspect",
    "ospathbasename_aspect",
    "ospathdirname_aspect",
    "ospathjoin_aspect",
    "ospathnormcase_aspect",
    "ospathsplit_aspect",
    "ospathsplitdrive_aspect",
    "ospathsplitext_aspect",
    "ospathsplitroot_aspect",
    "re_expand_aspect",
    "re_findall_aspect",
    "re_finditer_aspect",
    "re_fullmatch_aspect",
    "re_group_aspect",
    "re_groups_aspect",
    "re_match_aspect",
    "re_search_aspect",
    "re_sub_aspect",
    "re_subn_aspect",
    "replace_aspect",
    "repr_aspect",
    "rsplit_aspect",
    "slice_aspect",
    "split_aspect",
    "splitlines_aspect",
    "lstrip_aspect",
    "rstrip_aspect",
    "strip_aspect",
    "str_aspect",
    "stringio_aspect",
    "swapcase_aspect",
    "title_aspect",
    "translate_aspect",
    "upper_aspect",
]


def stringio_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> _io.StringIO:
    if orig_function is not None:
        if flag_added_args > 0:
            args = args[flag_added_args:]
        result = orig_function(*args, **kwargs)
    else:
        if flag_added_args > 0:
            args = args[flag_added_args:]
        result = _io.StringIO(*args, **kwargs)

    if args and is_pyobject_tainted(args[0]) and isinstance(result, _io.StringIO):
        try:
            copy_ranges_from_strings(args[0], result)
        except Exception as e:
            iast_propagation_error_log(f"stringio_aspect. {e}")
    return result


def bytesio_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> _io.BytesIO:
    if orig_function is not None:
        if flag_added_args > 0:
            args = args[flag_added_args:]
        result = orig_function(*args, **kwargs)
    else:
        if flag_added_args > 0:
            args = args[flag_added_args:]
        result = _io.BytesIO(*args, **kwargs)

    if args and is_pyobject_tainted(args[0]) and isinstance(result, _io.BytesIO):
        try:
            copy_ranges_from_strings(args[0], result)
        except Exception as e:
            iast_propagation_error_log(f"bytesio_aspect. {e}")
    return result


def bytes_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> bytes:
    if orig_function is not None:
        if orig_function != builtin_bytes:
            if flag_added_args > 0:
                args = args[flag_added_args:]
            return orig_function(*args, **kwargs)
        result = builtin_bytes(*args, **kwargs)
    else:
        result = args[0].bytes(*args[1:], **kwargs)

    if args and is_pyobject_tainted(args[0]):
        try:
            copy_ranges_from_strings(args[0], result)
        except Exception as e:
            iast_propagation_error_log(f"bytes_aspect. {e}")
    return result


def bytearray_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> bytearray:
    if orig_function is not None:
        if orig_function != builtin_bytearray:
            if flag_added_args > 0:
                args = args[flag_added_args:]
            return orig_function(*args, **kwargs)
        result = builtin_bytearray(*args, **kwargs)
    else:
        result = args[0].bytearray(*args[1:], **kwargs)

    if args and is_pyobject_tainted(args[0]):
        try:
            copy_ranges_from_strings(args[0], result)
        except Exception as e:
            iast_propagation_error_log(f"bytearray_aspect. {e}")
    return result


def join_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Any:
    if not orig_function:
        orig_function = args[0].join
    if not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    if not args:
        return orig_function(*args, **kwargs)

    joiner = args[0]
    args = args[flag_added_args:]

    return _join_aspect(joiner, *args, **kwargs)


def bytearray_extend_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Any:
    if orig_function is not None and not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    if len(args) < 2:
        # If we're not receiving at least 2 arguments, means the call was
        # ``x.extend()`` and not ``x.extend(y)``
        # so either not the extend we're looking for, or no changes in taint ranges.
        return args[0].extend(*args[1:], **kwargs)

    op1 = args[0]
    op2 = args[1]
    if not isinstance(op1, bytearray) or not isinstance(op2, (bytearray, bytes)):
        return op1.extend(*args[1:], **kwargs)
    try:
        return _extend_aspect(op1, op2)
    except Exception as e:
        iast_propagation_error_log(f"extend_aspect. {e}")
        return op1.extend(op2)


def build_string_aspect(*args: List[Any]) -> TEXT_TYPES:
    return join_aspect("".join, 1, "", args)


def ljust_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if not orig_function:
        orig_function = args[0].ljust
    if not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]

    result = candidate_text.ljust(*args, **kwargs)

    if isinstance(candidate_text, IAST.TEXT_TYPES):
        try:
            ranges_new = get_ranges(candidate_text)
            fillchar = parse_params(1, "fillchar", " ", *args, **kwargs)
            fillchar_ranges = get_ranges(fillchar)
            if ranges_new is None or (not ranges_new and not fillchar_ranges):
                return result

            if fillchar_ranges:
                # Can only be one char, so we create one range to cover from the start to the end
                ranges_new = ranges_new + [shift_taint_range(fillchar_ranges[0], len(candidate_text))]

            taint_pyobject_with_ranges(result, ranges_new)
            return result
        except Exception as e:
            iast_propagation_error_log(f"ljust_aspect. {e}")

    return result


def zfill_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]

    result = candidate_text.zfill(*args, **kwargs)

    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return result

    try:
        ranges_orig = get_ranges(candidate_text)
        if not ranges_orig:
            return result
        prefix = candidate_text[0] in ("-", "+")

        difflen = len(result) - len(candidate_text)
        ranges_new: List[TaintRange] = []
        ranges_new_append = ranges_new.append
        ranges_new_extend = ranges_new.extend

        for r in ranges_orig:
            if not prefix or r.start > 0:
                ranges_new_append(
                    TaintRange(start=r.start + difflen, length=r.length, source=r.source, secure_marks=r.secure_marks)
                )
            else:
                ranges_new_extend(
                    [
                        TaintRange(start=0, length=1, source=r.source, secure_marks=r.secure_marks),
                        TaintRange(
                            start=r.start + difflen + 1,
                            length=r.length - 1,
                            source=r.source,
                            secure_marks=r.secure_marks,
                        ),
                    ]
                )
        taint_pyobject_with_ranges(result, tuple(ranges_new))
    except Exception as e:
        iast_propagation_error_log(f"zfill_aspect. {e}")

    return result


def format_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is None:
        orig_function = args[0].format

    if not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    if not args:
        return orig_function(*args, **kwargs)

    candidate_text: Text = args[0]
    args = args[flag_added_args:]

    result = candidate_text.format(*args, **kwargs)

    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return result

    if isinstance(candidate_text, IAST.TEXT_TYPES):
        try:
            params = tuple(args) + tuple(kwargs.values())
            return _format_aspect(candidate_text, params, *args, **kwargs)
        except Exception as e:
            iast_propagation_error_log(f"format_aspect. {e}")

    return candidate_text.format(*args, **kwargs)


def format_map_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is None:
        orig_function = args[0].format_map

    if not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]

        return orig_function(*args, **kwargs)

    candidate_text: Text = args[0]
    args = args[flag_added_args:]

    result = candidate_text.format_map(*args, **kwargs)

    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return result

    try:
        mapping = parse_params(0, "mapping", None, *args, **kwargs)
        mapping_tuple = tuple(mapping if not isinstance(mapping, dict) else mapping.values())
        ranges_orig, candidate_text_ranges = are_all_text_all_ranges(
            candidate_text,
            args + mapping_tuple,
        )
        if not ranges_orig:
            return result

        return _convert_escaped_text_to_tainted_text(
            as_formatted_evidence(
                candidate_text, candidate_text_ranges, tag_mapping_function=TagMappingMode.Mapper
            ).format_map(
                {
                    key: (
                        as_formatted_evidence(value, tag_mapping_function=TagMappingMode.Mapper)
                        if isinstance(value, IAST.TEXT_TYPES)
                        else value
                    )
                    for key, value in mapping.items()
                }
                if isinstance(mapping, dict)
                else tuple(mapping)
            ),
            ranges_orig=ranges_orig,
        )
    except Exception as e:
        iast_propagation_error_log(f"format_map_aspect. {e}")

    return result


def repr_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Any:
    # DEV: We call this function directly passing None as orig_function
    if orig_function is not None and not (
        orig_function is repr or getattr(orig_function, "__name__", None) == "__repr__"
    ):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    result = repr(*args, **kwargs)

    if args and isinstance(args[0], IAST.TEXT_TYPES) and is_pyobject_tainted(args[0]):
        try:
            if isinstance(args[0], bytes):
                check_offset = ascii(args[0])[2:-1]
            elif isinstance(args[0], bytearray):
                check_offset = ascii(args[0])[12:-2]
            else:
                check_offset = args[0]
            try:
                offset = result.index(check_offset)
            except ValueError:
                offset = 0

            copy_and_shift_ranges_from_strings(args[0], result, offset, len(check_offset))
        except Exception as e:
            iast_propagation_error_log(f"repr_aspect. {e}")
    return result


def format_value_aspect(
    element: Any,
    options: int = 0,
    format_spec: Optional[str] = None,
) -> TEXT_TYPES:
    if options == 115:
        new_text = str_aspect(str, 0, element)
    elif options == 114:
        new_text = repr_aspect(repr, 0, element)
    elif options == 97:
        new_text = ascii(element)
    else:
        new_text = element
    if not isinstance(new_text, IAST.TEXT_TYPES):
        if format_spec:
            return format(new_text, format_spec)
        return format(new_text)

    if format_spec:
        new_new_text = f"{new_text:{format_spec}}"  # type: ignore[str-bytes-safe]
        try:
            # Apply formatting
            text_ranges = get_tainted_ranges(new_text)
            if text_ranges:
                try:
                    new_ranges = list()
                    for text_range in text_ranges:
                        new_ranges.append(shift_taint_range(text_range, new_new_text.index(new_text)))  # type: ignore
                    if new_ranges:
                        taint_pyobject_with_ranges(new_new_text, tuple(new_ranges))
                    return new_new_text
                except ValueError:
                    return new_new_text
            else:
                return new_new_text
        except Exception as e:
            iast_propagation_error_log(f"format_value_aspect. {e}")
            return new_new_text

    return format(new_text)


def incremental_translation(self, incr_coder, funcode, empty):
    tainted_ranges = iter(get_tainted_ranges(self))
    result_list, new_ranges = [], []
    result_length, i = 0, 0
    tainted_range = next(tainted_ranges, None)
    tainted_new_length = 0
    in_tainted = False
    tainted_start = 0
    bytes_iterated = 0
    try:
        for i in range(len(self)):
            if tainted_range is None:
                # no more tainted ranges, finish decoding all at once
                new_prod = funcode(self[i:])
                result_list.append(new_prod)
                break
            if i == tainted_range.start:
                # start new tainted range
                tainted_start = bytes_iterated
                tainted_new_length = 0
                in_tainted = True

            new_prod = funcode(self[i : i + 1])
            result_list.append(new_prod)
            result_length += len(new_prod)

            if in_tainted:
                tainted_new_length += len(new_prod)
            else:
                bytes_iterated += len(new_prod)

            if i + 1 == tainted_range.start + tainted_range.length and tainted_new_length > 0:
                # end range. Do no taint partial multi-bytes character that comes next.
                new_ranges.append(
                    TaintRange(
                        start=tainted_start,
                        length=tainted_new_length,
                        source=tainted_range.source,
                        secure_marks=tainted_range.secure_marks,
                    )
                )

                tainted_range = next(tainted_ranges, None)
        result_list.append(funcode(self[:0], True))
    except UnicodeDecodeError as e:
        offset = -len(incr_coder.getstate()[0])
        raise UnicodeDecodeError(e.args[0], self, i + e.args[2] + offset, i + e.args[3] + offset, *e.args[4:])
    except UnicodeEncodeError:
        funcode(self)
    result = empty.join(result_list)
    taint_pyobject_with_ranges(result, tuple(new_ranges))
    return result


def decode_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)

    self = args[0]
    args = args[(flag_added_args or 1) :]
    # Assume we call decode method of the first argument
    result = self.decode(*args, **kwargs)

    if is_pyobject_tainted(self) and isinstance(self, bytes):
        try:
            codec = args[0] if args else "utf-8"
            inc_dec = codecs.getincrementaldecoder(codec)(**kwargs)
            return incremental_translation(self, inc_dec, inc_dec.decode, "")
        except Exception as e:
            iast_propagation_error_log(f"decode_aspect. {e}")
    return result


def encode_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)

    self = args[0]
    args = args[(flag_added_args or 1) :]

    result = self.encode(*args, **kwargs)

    if is_pyobject_tainted(self) and isinstance(self, str):
        try:
            codec = args[0] if args else "utf-8"
            inc_enc = codecs.getincrementalencoder(codec)(**kwargs)
            return incremental_translation(self, inc_enc, inc_enc.encode, b"")
        except Exception as e:
            iast_propagation_error_log(f"encode_aspect. {e}")

    return result


def upper_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not isinstance(orig_function, BuiltinFunctionType) or not args):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return candidate_text.upper(*args, **kwargs)

    try:
        return common_replace("upper", candidate_text, *args, **kwargs)
    except Exception as e:
        iast_propagation_error_log(f"upper_aspect. {e}")
        return candidate_text.upper(*args, **kwargs)


def lower_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not isinstance(orig_function, BuiltinFunctionType) or not args):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return candidate_text.lower(*args, **kwargs)

    try:
        return common_replace("lower", candidate_text, *args, **kwargs)
    except Exception as e:
        iast_propagation_error_log(f"lower_aspect. {e}")
        return candidate_text.lower(*args, **kwargs)


def _distribute_ranges_and_escape(
    split_elements: List[Optional[TEXT_TYPES]],
    len_separator: int,
    ranges: Tuple[TaintRange, ...],
) -> List[Optional[TEXT_TYPES]]:
    # FIXME: converts to set, and then to list again, probably to remove
    # duplicates. This should be removed once the ranges values on the
    # taint dictionary are stored in a set.
    range_set = set(ranges)
    range_set_remove = range_set.remove
    formatted_elements: List[Optional[TEXT_TYPES]] = []
    formatted_elements_append = formatted_elements.append
    element_start = 0
    extra = 0

    for element in split_elements:
        if element is None:
            extra += len_separator
            continue
        # DEV: If this if is True, it means that the element is part of bytes/bytearray
        if isinstance(element, int):
            len_element = 1
        else:
            len_element = len(element)
        element_end = element_start + len_element
        new_ranges: Dict[TaintRange, TaintRange] = {}

        for taint_range in ranges:
            if (taint_range.start + taint_range.length) <= (element_start + extra):
                try:
                    range_set_remove(taint_range)
                except KeyError:
                    # If the range appears twice in ranges, it will be
                    # iterated twice, but it's only once in range_set,
                    # raising KeyError at remove, so it can be safely ignored
                    pass
                continue

            if taint_range.start > element_end:
                continue

            start = max(taint_range.start, element_start)
            end = min((taint_range.start + taint_range.length), element_end)
            if end <= start:
                continue

            if end - element_start < 1:
                continue

            new_range = TaintRange(
                start=start - element_start,
                length=end - element_start,
                source=taint_range.source,
                secure_marks=taint_range.secure_marks,
            )
            new_ranges[new_range] = taint_range

        element_ranges = tuple(new_ranges.keys())
        # DEV: If this if is True, it means that the element is part of bytes/bytearray
        if isinstance(element, int):
            element_new_id = new_pyobject_id(bytes([element]))
        else:
            element_new_id = new_pyobject_id(element)
        set_ranges(element_new_id, element_ranges)

        formatted_elements_append(
            as_formatted_evidence(
                element_new_id,
                element_ranges,
                TagMappingMode.Mapper_Replace,
                new_ranges,
            )
        )

        element_start = element_end + len_separator
    return formatted_elements


def aspect_replace_api(
    candidate_text: TEXT_TYPES, old_value: Any, new_value: Any, count: int, orig_result: Any
) -> TEXT_TYPES:
    ranges_orig, candidate_text_ranges = are_all_text_all_ranges(candidate_text, (old_value, new_value))
    if not ranges_orig:  # Ranges in args/kwargs are checked
        return orig_result

    empty = b"" if isinstance(candidate_text, (bytes, bytearray)) else ""

    if old_value:
        elements: List[Any] = candidate_text.split(old_value, count)
    else:
        if count == -1:
            elements = (
                [
                    empty,
                ]
                + (
                    list(candidate_text) if isinstance(candidate_text, str) else [bytes([x]) for x in candidate_text]  # type: ignore
                )
                + [
                    empty,
                ]
            )
        else:
            if isinstance(candidate_text, str):
                elements = (
                    [
                        empty,
                    ]
                    + list(candidate_text[: count - 1])
                    + [candidate_text[count - 1 :]]
                )
                if len(elements) == count and elements[-1] != "":
                    elements.append(empty)
            else:
                elements = (
                    [
                        empty,
                    ]
                    + [bytes([x]) for x in candidate_text[: count - 1]]
                    + [bytes([x for x in candidate_text[count - 1 :]])]
                )
                if len(elements) == count and elements[-1] != b"":
                    elements.append(empty)
    i = 0
    new_elements: List[Optional[TEXT_TYPES]] = []
    new_elements_append = new_elements.append

    # if new value is blank, _distribute_ranges_and_escape function doesn't
    # understand what is the replacement to move the ranges.
    # In the other hand, Split function splits a string and the occurrence is
    # in the first or last position, split adds ''. IE:
    # 'XabcX'.split('X') -> ['', 'abc', '']
    # We add "None" in the old position and _distribute_ranges_and_escape
    # knows that this is the position of a old value and move len(old_value)
    # positions of the range
    if new_value in ("", b""):
        len_elements = len(elements)
        for element in elements:
            if i == 0 and element in ("", b""):
                new_elements_append(None)
                i += 1
                continue
            if i + 1 == len_elements and element in ("", b""):
                new_elements_append(None)
                continue

            new_elements_append(element)

            if count < 0 and i + 1 < len(elements):
                new_elements_append(None)
            elif i >= count and i + 1 < len(elements):
                new_elements_append(old_value)
            i += 1
    else:
        new_elements = elements

    if candidate_text_ranges:
        new_elements = _distribute_ranges_and_escape(
            new_elements,
            len(old_value),
            candidate_text_ranges,
        )
    else:
        new_elements = [element for element in new_elements if element is not None]

    result_formatted = as_formatted_evidence(new_value, tag_mapping_function=TagMappingMode.Mapper).join(new_elements)

    result = _convert_escaped_text_to_tainted_text(
        result_formatted,
        ranges_orig=ranges_orig,
    )

    return result


def replace_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not isinstance(orig_function, BuiltinFunctionType) or not args):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]
    orig_result = candidate_text.replace(*args, **kwargs)
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return orig_result

    ###
    # Optimization: if we're not going to replace, just return the original string
    count = parse_params(2, "count", -1, *args, **kwargs)
    if count == 0:
        return candidate_text
    ###
    try:
        old_value = parse_params(0, "old_value", None, *args, **kwargs)
        new_value = parse_params(1, "new_value", None, *args, **kwargs)

        if old_value is None or new_value is None:
            return orig_result

        if old_value not in candidate_text or old_value == new_value:
            return candidate_text

        if orig_result in ("", b"", bytearray(b"")):
            return orig_result

        if count < -1:
            count = -1

        aspect_result = aspect_replace_api(candidate_text, old_value, new_value, count, orig_result)

        if aspect_result != orig_result:
            return orig_result

        return aspect_result
    except Exception as e:
        iast_propagation_error_log(f"replace_aspect. {e}")
        return orig_result


def swapcase_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not isinstance(orig_function, BuiltinFunctionType) or not args):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return candidate_text.swapcase(*args, **kwargs)
    try:
        return common_replace("swapcase", candidate_text, *args, **kwargs)
    except Exception as e:
        iast_propagation_error_log(f"swapcase_aspect. {e}")
        return candidate_text.swapcase(*args, **kwargs)


def title_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not isinstance(orig_function, BuiltinFunctionType) or not args):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return candidate_text.title(*args, **kwargs)
    try:
        return common_replace("title", candidate_text, *args, **kwargs)
    except Exception as e:
        iast_propagation_error_log(f"title_aspect. {e}")
        return candidate_text.title(*args, **kwargs)


def capitalize_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not isinstance(orig_function, BuiltinFunctionType) or not args):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return candidate_text.capitalize(*args, **kwargs)

    try:
        return common_replace("capitalize", candidate_text, *args, **kwargs)
    except Exception as e:
        iast_propagation_error_log(f"capitalize_aspect. {e}")
        return candidate_text.capitalize(*args, **kwargs)


def casefold_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None:
        if not isinstance(orig_function, BuiltinFunctionType) or not args:
            if flag_added_args > 0:
                args = args[flag_added_args:]
            return orig_function(*args, **kwargs)
    else:
        orig_function = getattr(args[0], "casefold", None)

    if orig_function is not None and getattr(orig_function, "__qualname__") not in (
        "str.casefold",
        "bytes.casefold",
        "bytearray.casefold",
    ):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return candidate_text.casefold(*args, **kwargs)
    try:
        return common_replace("casefold", candidate_text, *args, **kwargs)
    except Exception as e:
        iast_propagation_error_log(f"casefold_aspect. {e}")
        return candidate_text.casefold(*args, **kwargs)  # type: ignore[union-attr]


def translate_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not isinstance(orig_function, BuiltinFunctionType) or not args):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return candidate_text.translate(*args, **kwargs)
    try:
        return common_replace("translate", candidate_text, *args, **kwargs)
    except Exception as e:
        iast_propagation_error_log(f"translate_aspect. {e}")
        return candidate_text.translate(*args, **kwargs)


def empty_func(*args, **kwargs):
    pass


def re_findall_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES, Tuple[TEXT_TYPES, int]]:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].findall

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)

    try:
        if not isinstance(self, (Pattern, ModuleType)):
            # This is not the sub we're looking for
            return result
        elif isinstance(self, ModuleType):
            if self.__name__ != "re" or self.__package__ not in ("", "re"):
                return result
            # In this case, the first argument is the pattern
            # which we don't need to check for tainted ranges
            args = args[1:]
        elif not isinstance(result, list) or not len(result):
            return result

        if len(args) >= 1:
            string = args[0]
            ranges = get_tainted_ranges(string)
            if ranges:
                result = copy_ranges_to_iterable_with_strings(result, ranges)
    except Exception as e:
        iast_propagation_error_log(f"re_findall_aspect. {e}")

    return result


def re_finditer_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Iterator:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].finditer

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)
    try:
        if not isinstance(self, (Pattern, ModuleType)):
            # This is not the sub we're looking for
            return result
        elif isinstance(self, ModuleType):
            if self.__name__ != "re" or self.__package__ not in ("", "re"):
                return result
            # In this case, the first argument is the pattern
            # which we don't need to check for tainted ranges
            args = args[1:]

        elif not isinstance(result, Iterator):
            return result

        if len(args) >= 1:
            string = args[0]
            if is_pyobject_tainted(string):
                ranges = get_ranges(string)
                result, result_backup = itertools.tee(result)
                for elem in result_backup:
                    taint_pyobject_with_ranges(elem, ranges)
    except Exception as e:
        iast_propagation_error_log(f"IAST propagation error. re_finditer_aspect. {e}")
    return result


def re_sub_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].sub

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)

    if not isinstance(self, (Pattern, ModuleType)):
        # This is not the sub we're looking for
        return result
    elif isinstance(self, ModuleType):
        if self.__name__ != "re" or self.__package__ not in ("", "re"):
            return result
        # In this case, the first argument is the pattern
        # which we don't need to check for tainted ranges
        args = args[1:]

    if len(args) >= 2 and len(result):
        repl = args[0]
        string = args[1]
        if is_pyobject_tainted(string):
            # Taint result
            copy_and_shift_ranges_from_strings(string, result, 0, len(result))
        elif is_pyobject_tainted(repl):
            # Taint result
            copy_and_shift_ranges_from_strings(repl, result, 0, len(result))

    return result


def re_subn_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES, Tuple[TEXT_TYPES, int]]:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].subn

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)

    if not isinstance(self, (Pattern, ModuleType)):
        # This is not the sub we're looking for
        return result
    elif isinstance(self, ModuleType):
        if self.__name__ != "re" or self.__package__ not in ("", "re"):
            return result
        # In this case, the first argument is the pattern
        # which we don't need to check for tainted ranges
        args = args[1:]
    elif not isinstance(result, tuple) and not len(result) == 2:
        return result

    new_string, number = result

    if len(args) >= 2:
        repl = args[0]
        string = args[1]
        if is_pyobject_tainted(string):
            # Taint result
            copy_and_shift_ranges_from_strings(string, new_string, 0, len(new_string))
        elif is_pyobject_tainted(repl):
            # Taint result
            copy_and_shift_ranges_from_strings(repl, new_string, 0, len(new_string))

    return (new_string, number)


def re_match_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Any:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].match

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)

    offset = 0
    if isinstance(self, ModuleType):
        if self.__name__ != "re" or self.__package__ not in ("", "re") or not isinstance(result, Match):
            return result
    elif isinstance(self, Pattern):
        offset = -1

    if len(args) >= 2 + offset and is_pyobject_tainted(args[1 + offset]):
        taint_pyobject_with_ranges(result, get_ranges(args[1 + offset]))

    return result


def re_fullmatch_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Any:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].fullmatch

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)

    offset = 0
    if isinstance(self, ModuleType):
        if self.__name__ != "re" or self.__package__ not in ("", "re") or not isinstance(result, Match):
            return result
    elif isinstance(self, Pattern):
        offset = -1

    if len(args) >= 2 + offset and is_pyobject_tainted(args[1 + offset]):
        taint_pyobject_with_ranges(result, get_ranges(args[1 + offset]))

    return result


def re_search_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Any:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].search

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)

    offset = 0
    if isinstance(self, ModuleType):
        if self.__name__ != "re" or self.__package__ not in ("", "re") or not isinstance(result, Match):
            return result
    elif isinstance(self, Pattern):
        offset = -1

    if len(args) >= 2 + offset and is_pyobject_tainted(args[1 + offset]):
        taint_pyobject_with_ranges(result, get_ranges(args[1 + offset]))

    return result


def re_groups_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Any:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].groups

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)

    if not result or not isinstance(self, Match) or not is_pyobject_tainted(self):
        return result

    try:
        return copy_ranges_to_iterable_with_strings(result, get_ranges(self))
    except Exception as e:
        iast_propagation_error_log(f"re_groups_aspect. {e}")
        return result


def re_group_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Any:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].group

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)

    if not result or not isinstance(self, Match) or not is_pyobject_tainted(self):
        return result

    try:
        if isinstance(result, tuple):
            result = copy_ranges_to_iterable_with_strings(result, get_ranges(self))
        else:
            result = copy_ranges_to_string(result, get_ranges(self))
    except Exception as e:
        iast_propagation_error_log(f"re_group_aspect. {e}")

    return result


def re_expand_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> Any:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)
    elif orig_function is None:
        orig_function = args[0].expand

    self = args[0]
    args = args[(flag_added_args or 1) :]
    result = orig_function(*args, **kwargs)

    if not result or not isinstance(self, Match):
        # No need to taint the result
        return result

    try:
        if not is_pyobject_tainted(self) and len(args) and not is_pyobject_tainted(args[0]):
            # Nothing tainted, no need to taint the result either
            return result

        if is_pyobject_tainted(self):
            result = copy_ranges_to_string(result, get_ranges(self))
        elif is_pyobject_tainted(args[0]):
            result = copy_ranges_to_string(result, get_ranges(args[0]))
    except Exception as e:
        iast_propagation_error_log(f"re_expand_aspect. {e}")

    return result


def ospathjoin_aspect(*args: Any, **kwargs: Any) -> Any:
    if all(isinstance(arg, IAST.TEXT_TYPES) for arg in args):
        try:
            return _aspect_ospathjoin(*args, **kwargs)
        except Exception as e:
            iast_propagation_error_log(f"ospathjoin_aspect. {e}")

    return os.path.join(*args, **kwargs)


def ospathbasename_aspect(*args: Any, **kwargs: Any) -> Any:
    if all(isinstance(arg, IAST.TEXT_TYPES) for arg in args):
        try:
            return _aspect_ospathbasename(*args, **kwargs)
        except Exception as e:
            iast_propagation_error_log(f"_aspect_ospathbasename. {e}")

    return os.path.basename(*args, **kwargs)


def ospathdirname_aspect(*args: Any, **kwargs: Any) -> Any:
    if all(isinstance(arg, IAST.TEXT_TYPES) for arg in args):
        try:
            return _aspect_ospathdirname(*args, **kwargs)
        except Exception as e:
            iast_propagation_error_log(f"_aspect_ospathdirname. {e}")

    return os.path.dirname(*args, **kwargs)


def ospathnormcase_aspect(*args: Any, **kwargs: Any) -> Any:
    if all(isinstance(arg, IAST.TEXT_TYPES) for arg in args):
        try:
            return _aspect_ospathnormcase(*args, **kwargs)
        except Exception as e:
            iast_propagation_error_log(f"ospathnormcase_aspect. {e}")

    return os.path.normcase(*args, **kwargs)


def ospathsplit_aspect(*args: Any, **kwargs: Any) -> Any:
    if all(isinstance(arg, IAST.TEXT_TYPES) for arg in args):
        try:
            return _aspect_ospathsplit(*args, **kwargs)
        except Exception as e:
            iast_propagation_error_log(f"ospathnormcase_aspect. {e}")

    return os.path.split(*args, **kwargs)


def ospathsplitdrive_aspect(*args: Any, **kwargs: Any) -> Any:
    if all(isinstance(arg, IAST.TEXT_TYPES) for arg in args):
        try:
            return _aspect_ospathsplitdrive(*args, **kwargs)
        except Exception as e:
            iast_propagation_error_log(f"_aspect_ospathsplitdrive. {e}")

    return os.path.splitdrive(*args, **kwargs)


def ospathsplitext_aspect(*args: Any, **kwargs: Any) -> Any:
    if all(isinstance(arg, IAST.TEXT_TYPES) for arg in args):
        try:
            return _aspect_ospathsplitext(*args, **kwargs)
        except Exception as e:
            iast_propagation_error_log(f"_aspect_ospathsplitext. {e}")

    return os.path.splitext(*args, **kwargs)


def ospathsplitroot_aspect(*args: Any, **kwargs: Any) -> Any:
    if all(isinstance(arg, IAST.TEXT_TYPES) for arg in args):
        try:
            return _aspect_ospathsplitroot(*args, **kwargs)
        except Exception as e:
            iast_propagation_error_log(f"_aspect_ospathsplitroot. {e}")

    return os.path.splitroot(*args, **kwargs)  # type: ignore[attr-defined]


def lstrip_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]

    result = candidate_text.lstrip(*args, **kwargs)

    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return result

    try:
        _strip_lstrip_aspect(candidate_text, result)
        return result
    except Exception as e:
        iast_propagation_error_log(f"lstrip_aspect. {e}")

    return result


def rstrip_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]

    result = candidate_text.rstrip(*args, **kwargs)

    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return result

    try:
        ranges_new: List[TaintRange] = []
        ranges_new_append = ranges_new.append

        ranges = get_ranges(candidate_text)
        len_result = len(result)
        if len_result == len(candidate_text):
            taint_pyobject_with_ranges(result, tuple(ranges))
        else:
            for taint_range in ranges:
                if taint_range.start >= len_result:
                    continue

                new_length = min(len_result - taint_range.start, taint_range.length)
                new_range = TaintRange(
                    start=taint_range.start,
                    length=new_length,
                    source=taint_range.source,
                    secure_marks=taint_range.secure_marks,
                )
                ranges_new_append(new_range)
            taint_pyobject_with_ranges(result, tuple(ranges_new))
        return result
    except Exception as e:
        iast_propagation_error_log(f"rstrip_aspect. {e}")

    return result


def strip_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> TEXT_TYPES:
    if orig_function is not None and not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]
    result = candidate_text.strip(*args, **kwargs)

    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return result

    try:
        _strip_lstrip_aspect(candidate_text, result)
        return result
    except Exception as e:
        iast_propagation_error_log(f"strip_aspect. {e}")

    return result


def _strip_lstrip_aspect(candidate_text, result):
    ranges_new: List[TaintRange] = []
    ranges = get_ranges(candidate_text)
    start_pos = candidate_text.index(result)
    len_result = len(result)
    end_pos = start_pos + len_result
    if len_result != len(candidate_text):
        for taint_range in ranges:
            range_start = taint_range.start
            range_end = range_start + taint_range.length

            if range_end <= start_pos or range_start >= end_pos:
                continue

            # Calculate new range boundaries
            new_start = max(range_start - start_pos, 0)
            new_end = min(range_end - start_pos, len_result)
            new_length = new_end - new_start

            if new_length > 0:
                # Create a new range with adjusted position and length
                new_range = TaintRange(
                    start=new_start,
                    length=new_length,
                    source=taint_range.source,
                    secure_marks=taint_range.secure_marks,
                )
                ranges_new.append(new_range)
        taint_pyobject_with_ranges(result, tuple(ranges_new))
