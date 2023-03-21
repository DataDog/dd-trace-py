#!/usr/bin/env python3

from builtins import str as builtin_str
import codecs
import threading

from ddtrace.appsec.iast._input_info import Input_info
from ddtrace.appsec.iast._taint_tracking import add_taint_pyobject  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import set_tainted_ranges  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import taint_pyobject  # type: ignore[attr-defined]


def str_aspect(*args, **kwargs):
    result = builtin_str(*args, **kwargs)
    thread_id = threading.current_thread().ident
    if isinstance(args[0], (str, bytes, bytearray)) and is_pyobject_tainted(args[0], thread_id):
        result = taint_pyobject(result, Input_info("str_aspect", result, 0), thread_id)

    return result


def add_aspect(op1, op2):
    if not isinstance(op1, (str, bytes, bytearray)):
        return op1 + op2
    res = getattr(op1.__class__, "__add__")(op1, op2)
    if res is op1 or res is op2:
        return res
    thread_id = threading.current_thread().ident
    return add_taint_pyobject(res, op1, op2, thread_id)


def incremental_translation(self, incr_coder, funcode, empty):
    thread_id = threading.current_thread().ident
    tainted_ranges = iter(get_tainted_ranges(self, thread_id))
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
    set_tainted_ranges(result, new_ranges, thread_id)
    return result


def decode_aspect(self, *args, **kwargs):
    thread_id = threading.current_thread().ident
    if not isinstance(self, bytes) or not is_pyobject_tainted(self, thread_id):
        return self.decode(*args, **kwargs)
    codec = args[0] if args else "utf-8"
    inc_dec = codecs.getincrementaldecoder(codec)(**kwargs)
    return incremental_translation(self, inc_dec, inc_dec.decode, "")


def encode_aspect(self, *args, **kwargs):
    thread_id = threading.current_thread().ident
    if not isinstance(self, str) or not is_pyobject_tainted(self, thread_id):
        return self.encode(*args, **kwargs)
    codec = args[0] if args else "utf-8"
    inc_enc = codecs.getincrementalencoder(codec)(**kwargs)
    return incremental_translation(self, inc_enc, inc_enc.encode, b"")
