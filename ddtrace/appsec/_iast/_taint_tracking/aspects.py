from builtins import bytearray as builtin_bytearray
from builtins import bytes as builtin_bytes
from builtins import str as builtin_str
import codecs
from re import Pattern
from types import BuiltinFunctionType
from types import ModuleType
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Text
from typing import Tuple
from typing import Union

from ddtrace.appsec._constants import IAST

from .._taint_tracking import TagMappingMode
from .._taint_tracking import TaintRange
from .._taint_tracking import _aspect_ospathbasename
from .._taint_tracking import _aspect_ospathdirname
from .._taint_tracking import _aspect_ospathjoin
from .._taint_tracking import _aspect_ospathnormcase
from .._taint_tracking import _aspect_ospathsplit
from .._taint_tracking import _aspect_ospathsplitdrive
from .._taint_tracking import _aspect_ospathsplitext
from .._taint_tracking import _aspect_ospathsplitroot
from .._taint_tracking import _aspect_rsplit
from .._taint_tracking import _aspect_split
from .._taint_tracking import _aspect_splitlines
from .._taint_tracking import _convert_escaped_text_to_tainted_text
from .._taint_tracking import _format_aspect
from .._taint_tracking import are_all_text_all_ranges
from .._taint_tracking import as_formatted_evidence
from .._taint_tracking import common_replace
from .._taint_tracking import copy_and_shift_ranges_from_strings
from .._taint_tracking import copy_ranges_from_strings
from .._taint_tracking import get_ranges
from .._taint_tracking import get_tainted_ranges
from .._taint_tracking import iast_taint_log_error
from .._taint_tracking import is_pyobject_tainted
from .._taint_tracking import new_pyobject_id
from .._taint_tracking import parse_params
from .._taint_tracking import set_ranges
from .._taint_tracking import shift_taint_range
from .._taint_tracking import taint_pyobject_with_ranges
from .._taint_tracking._native import aspects  # noqa: F401


TEXT_TYPES = Union[str, bytes, bytearray]


_add_aspect = aspects.add_aspect
_extend_aspect = aspects.extend_aspect
_index_aspect = aspects.index_aspect
_join_aspect = aspects.join_aspect
_slice_aspect = aspects.slice_aspect

__all__ = [
    "add_aspect",
    "str_aspect",
    "bytearray_extend_aspect",
    "decode_aspect",
    "encode_aspect",
    "re_sub_aspect",
    "_aspect_ospathjoin",
    "_aspect_split",
    "_aspect_rsplit",
    "_aspect_splitlines",
    "_aspect_ospathbasename",
    "_aspect_ospathdirname",
    "_aspect_ospathnormcase",
    "_aspect_ospathsplit",
    "_aspect_ospathsplitext",
    "_aspect_ospathsplitdrive",
    "_aspect_ospathsplitroot",
]

# TODO: Factorize the "flags_added_args" copypasta into a decorator


def add_aspect(op1, op2):
    if not isinstance(op1, IAST.TEXT_TYPES) or not isinstance(op2, IAST.TEXT_TYPES) or type(op1) != type(op2):
        return op1 + op2
    try:
        return _add_aspect(op1, op2)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. add_aspect. {}".format(e))
    return op1 + op2


def split_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> str:
    if orig_function is not None:
        if orig_function != builtin_str:
            if flag_added_args > 0:
                args = args[flag_added_args:]
            return orig_function(*args, **kwargs)
    try:
        return _aspect_split(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. split_aspect. {}".format(e))
        return args[0].split(*args[1:], **kwargs)


def rsplit_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> str:
    if orig_function is not None:
        if orig_function != builtin_str:
            if flag_added_args > 0:
                args = args[flag_added_args:]
            return orig_function(*args, **kwargs)
    try:
        return _aspect_rsplit(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. rsplit_aspect. {}".format(e))
        return args[0].rsplit(*args[1:], **kwargs)


def splitlines_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> str:
    if orig_function:
        if orig_function != builtin_str:
            if flag_added_args > 0:
                args = args[flag_added_args:]
            return orig_function(*args, **kwargs)
    try:
        return _aspect_splitlines(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. splitlines_aspect. {}".format(e))
        return args[0].splitlines(*args[1:], **kwargs)


def str_aspect(orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any) -> str:
    if orig_function is not None:
        if orig_function != builtin_str:
            if flag_added_args > 0:
                args = args[flag_added_args:]
            return orig_function(*args, **kwargs)
        result = builtin_str(*args, **kwargs)
    else:
        result = args[0].str(*args[1:], **kwargs)

    if args and is_pyobject_tainted(args[0]):
        try:
            if isinstance(args[0], (bytes, bytearray)):
                encoding = parse_params(1, "encoding", "utf-8", *args, **kwargs)
                errors = parse_params(2, "errors", "strict", *args, **kwargs)
                check_offset = args[0].decode(encoding, errors)
            else:
                check_offset = args[0]
            offset = result.index(check_offset)
            copy_and_shift_ranges_from_strings(args[0], result, offset)
        except Exception as e:
            iast_taint_log_error("IAST propagation error. str_aspect. {}".format(e))
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
            iast_taint_log_error("IAST propagation error. bytes_aspect. {}".format(e))
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
            iast_taint_log_error("IAST propagation error. bytearray_aspect. {}".format(e))
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
    if not isinstance(joiner, IAST.TEXT_TYPES):
        return joiner.join(*args, **kwargs)
    try:
        return _join_aspect(joiner, *args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. join_aspect. {}".format(e))
        return joiner.join(*args, **kwargs)


def index_aspect(candidate_text: Text, index: int) -> Text:
    if isinstance(candidate_text, IAST.TEXT_TYPES) and isinstance(index, int):
        try:
            return _index_aspect(candidate_text, index)
        except Exception as e:
            iast_taint_log_error("IAST propagation error. index_aspect. {}".format(e))

    return candidate_text[index]


def slice_aspect(candidate_text: Text, start: int, stop: int, step: int) -> Text:
    if (
        not isinstance(candidate_text, IAST.TEXT_TYPES)
        or (start is not None and not isinstance(start, int))
        or (stop is not None and not isinstance(stop, int))
        or (step is not None and not isinstance(step, int))
    ):
        return candidate_text[start:stop:step]

    try:
        return _slice_aspect(candidate_text, start, stop, step)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. slice_aspect. {}".format(e))
    return candidate_text[start:stop:step]


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
        iast_taint_log_error("IAST propagation error. extend_aspect. {}".format(e))
        return op1.extend(op2)


def modulo_aspect(candidate_text: Text, candidate_tuple: Any) -> Any:
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
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
                (
                    as_formatted_evidence(
                        parameter,
                        tag_mapping_function=TagMappingMode.Mapper,
                    )
                    if isinstance(parameter, IAST.TEXT_TYPES)
                    else parameter
                )
                for parameter in parameter_list
            ),
            ranges_orig=ranges_orig,
        )
    except Exception as e:
        iast_taint_log_error("IAST propagation error. modulo_aspect. {}".format(e))
        return candidate_text % candidate_tuple


def build_string_aspect(*args: List[Any]) -> TEXT_TYPES:
    return join_aspect("".join, 1, "", args)


def ljust_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
    if not orig_function:
        orig_function = args[0].ljust
    if not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    candidate_text = args[0]
    args = args[flag_added_args:]

    if isinstance(candidate_text, IAST.TEXT_TYPES):
        try:
            ranges_new = get_ranges(candidate_text)
            fillchar = parse_params(1, "fillchar", " ", *args, **kwargs)
            fillchar_ranges = get_ranges(fillchar)
            if ranges_new is None or (not ranges_new and not fillchar_ranges):
                return candidate_text.ljust(*args, **kwargs)

            if fillchar_ranges:
                # Can only be one char, so we create one range to cover from the start to the end
                ranges_new = ranges_new + [shift_taint_range(fillchar_ranges[0], len(candidate_text))]

            result = candidate_text.ljust(parse_params(0, "width", None, *args, **kwargs), fillchar)
            taint_pyobject_with_ranges(result, ranges_new)
            return result
        except Exception as e:
            iast_taint_log_error("IAST propagation error. ljust_aspect. {}".format(e))

    return candidate_text.ljust(*args, **kwargs)


def zfill_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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
                ranges_new_append(TaintRange(start=r.start + difflen, length=r.length, source=r.source))
            else:
                ranges_new_extend(
                    [
                        TaintRange(start=0, length=1, source=r.source),
                        TaintRange(start=r.start + difflen + 1, length=r.length - 1, source=r.source),
                    ]
                )
        taint_pyobject_with_ranges(result, tuple(ranges_new))
    except Exception as e:
        iast_taint_log_error("IAST propagation error. format_aspect. {}".format(e))

    return result


def format_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
    if not orig_function:
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
            iast_taint_log_error("IAST propagation error. format_aspect. {}".format(e))

    return candidate_text.format(*args, **kwargs)


def format_map_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
    if orig_function is not None and not isinstance(orig_function, BuiltinFunctionType):
        if flag_added_args > 0:
            args = args[flag_added_args:]
        return orig_function(*args, **kwargs)

    if orig_function is not None and not args:
        return orig_function(*args, **kwargs)

    candidate_text: Text = args[0]
    args = args[flag_added_args:]
    if not isinstance(candidate_text, IAST.TEXT_TYPES):
        return candidate_text.format_map(*args, **kwargs)

    try:
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
                    key: (
                        as_formatted_evidence(value, tag_mapping_function=TagMappingMode.Mapper)
                        if isinstance(value, IAST.TEXT_TYPES)
                        else value
                    )
                    for key, value in mapping.items()
                }
            ),
            ranges_orig=ranges_orig,
        )
    except Exception as e:
        iast_taint_log_error("IAST propagation error. format_map_aspect. {}".format(e))
        return candidate_text.format_map(*args, **kwargs)


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
            iast_taint_log_error("IAST propagation error. repr_aspect. {}".format(e))
    return result


def format_value_aspect(
    element: Any,
    options: int = 0,
    format_spec: Optional[str] = None,
) -> str:
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

    try:
        if format_spec:
            # Apply formatting
            text_ranges = get_tainted_ranges(new_text)
            if text_ranges:
                new_new_text = ("{:%s}" % format_spec).format(new_text)
                try:
                    new_ranges = list()
                    for text_range in text_ranges:
                        new_ranges.append(shift_taint_range(text_range, new_new_text.index(new_text)))
                    if new_ranges:
                        taint_pyobject_with_ranges(new_new_text, tuple(new_ranges))
                    return new_new_text
                except ValueError:
                    return ("{:%s}" % format_spec).format(new_text)
            else:
                return ("{:%s}" % format_spec).format(new_text)
        else:
            return format(new_text)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. format_value_aspect. {}".format(e))
        return new_text


def ospathjoin_aspect(*args, **kwargs):
    try:
        return _aspect_ospathjoin(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. ospathjoin_aspect. {}".format(e))
        import os.path

        return os.path.join(*args, **kwargs)


def ospathbasename_aspect(*args, **kwargs):
    try:
        return _aspect_ospathbasename(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. ospathbasename_aspect. {}".format(e))
        import os.path

        return os.path.basename(*args, **kwargs)


def ospathdirname_aspect(*args, **kwargs):
    try:
        return _aspect_ospathdirname(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. ospathdirname_aspect. {}".format(e))
        import os.path

        return os.path.dirname(*args, **kwargs)


def ospathsplit_aspect(*args, **kwargs):
    try:
        return _aspect_ospathsplit(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. ospathsplit_aspect. {}".format(e))
        import os.path

        return os.path.split(*args, **kwargs)


def ospathsplitext_aspect(*args, **kwargs):
    try:
        return _aspect_ospathsplitext(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. ospathsplitext_aspect. {}".format(e))
        import os.path

        return os.path.splitext(*args, **kwargs)


def ospathsplitroot_aspect(*args, **kwargs):
    try:
        return _aspect_ospathsplitroot(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. ospathsplitroot_aspect. {}".format(e))
        import os.path

        return os.path.splitroot(*args, **kwargs)


def ospathnormcase_aspect(*args, **kwargs):
    try:
        return _aspect_ospathnormcase(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. ospathnormcase_aspect. {}".format(e))
        import os.path

        return os.path.normcase(*args, **kwargs)


def ospathsplitdrive_aspect(*args, **kwargs):
    try:
        return _aspect_ospathsplitdrive(*args, **kwargs)
    except Exception as e:
        iast_taint_log_error("IAST propagation error. ospathsplitdrive_aspect. {}".format(e))
        import os.path

        return os.path.splitdrive(*args, **kwargs)


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


def decode_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)

    self = args[0]
    args = args[(flag_added_args or 1) :]
    # Assume we call decode method of the first argument

    if is_pyobject_tainted(self) and isinstance(self, bytes):
        try:
            codec = args[0] if args else "utf-8"
            inc_dec = codecs.getincrementaldecoder(codec)(**kwargs)
            return incremental_translation(self, inc_dec, inc_dec.decode, "")
        except Exception as e:
            iast_taint_log_error("IAST propagation error. decode_aspect. {}".format(e))
    return self.decode(*args, **kwargs)


def encode_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
    if orig_function is not None and (not flag_added_args or not args):
        # This patch is unexpected, so we fallback
        # to executing the original function
        return orig_function(*args, **kwargs)

    self = args[0]
    args = args[(flag_added_args or 1) :]

    if is_pyobject_tainted(self) and isinstance(self, str):
        try:
            codec = args[0] if args else "utf-8"
            inc_enc = codecs.getincrementalencoder(codec)(**kwargs)
            return incremental_translation(self, inc_enc, inc_enc.encode, b"")
        except Exception as e:
            iast_taint_log_error("IAST propagation error. encode_aspect. {}".format(e))
    result = self.encode(*args, **kwargs)
    return result


def upper_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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
        iast_taint_log_error("IAST propagation error. upper_aspect. {}".format(e))
        return candidate_text.upper(*args, **kwargs)


def lower_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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
        iast_taint_log_error("IAST propagation error. lower_aspect. {}".format(e))
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

    result_formatted = as_formatted_evidence(new_value, tag_mapping_function=TagMappingMode.Mapper).join(new_elements)

    result = _convert_escaped_text_to_tainted_text(
        result_formatted,
        ranges_orig=ranges_orig,
    )

    return result


def replace_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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
        iast_taint_log_error("IAST propagation error. replace_aspect. {}".format(e))
        return orig_result


def swapcase_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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
        iast_taint_log_error("IAST propagation error. swapcase_aspect. {}".format(e))
        return candidate_text.swapcase(*args, **kwargs)


def title_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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
        iast_taint_log_error("IAST propagation error. title_aspect. {}".format(e))
        return candidate_text.title(*args, **kwargs)


def capitalize_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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
        iast_taint_log_error("IAST propagation error. capitalize_aspect. {}".format(e))
        return candidate_text.capitalize(*args, **kwargs)


def casefold_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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
        iast_taint_log_error("IAST propagation error. casefold_aspect. {}".format(e))
        return candidate_text.casefold(*args, **kwargs)  # type: ignore[union-attr]


def translate_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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
        iast_taint_log_error("IAST propagation error. translate_aspect. {}".format(e))
        return candidate_text.translate(*args, **kwargs)


def empty_func(*args, **kwargs):
    pass


def re_sub_aspect(
    orig_function: Optional[Callable], flag_added_args: int, *args: Any, **kwargs: Any
) -> Union[TEXT_TYPES]:
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

    if len(args) >= 2:
        repl = args[0]
        string = args[1]
        if is_pyobject_tainted(string):
            # Taint result
            copy_and_shift_ranges_from_strings(string, result, 0, len(result))
        elif is_pyobject_tainted(repl):
            # Taint result
            copy_and_shift_ranges_from_strings(repl, result, 0, len(result))

    return result
