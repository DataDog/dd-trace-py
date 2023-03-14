#!/usr/bin/env python3

from builtins import str as builtin_str
import codecs

from ddtrace.appsec.iast._input_info import Input_info
from ddtrace.appsec.iast._taint_tracking import add_taint_pyobject  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import set_tainted_ranges  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import taint_pyobject  # type: ignore[attr-defined]


def str_aspect(*args, **kwargs):
    result = builtin_str(*args, **kwargs)
    if isinstance(args[0], (str, bytes, bytearray)) and is_pyobject_tainted(args[0]):
        result = taint_pyobject(result, Input_info("str_aspect", result, 0))

    return result


def add_aspect(op1, op2):
    if not isinstance(op1, (str, bytes, bytearray)):
        return op1 + op2

    return add_taint_pyobject(getattr(op1.__class__, "__add__")(op1, op2), op1, op2)


def decode_aspect(self, *args, **kwargs):
    if not is_pyobject_tainted(self) or not isinstance(self, bytes):
        return self.decode(*args, **kwargs)
    codec = args[0] if args else "utf-8"
    tainted_ranges = get_tainted_ranges(self)
    inc_dec = codecs.getincrementaldecoder(codec)(**kwargs)
    res = []
    pos = 0
    id_range = 0
    new_ranges = []
    i = 0
    try:
        for i in range(len(self)):
            if id_range < len(tainted_ranges) and i == tainted_ranges[id_range][1]:  # start
                new_ranges.append([tainted_ranges[id_range][0], pos])
            new_prod = inc_dec.decode(self[i : i + 1])
            res.append(new_prod)
            pos += len(new_prod)
            if (
                id_range < len(tainted_ranges) and i + 1 == tainted_ranges[id_range][1] + tainted_ranges[id_range][2]
            ):  # end_range
                new_ranges[-1].append(pos)
                id_range += 1
        res.append(inc_dec.decode(b"", True))
    except UnicodeDecodeError as e:
        raise UnicodeDecodeError(
            e.args[0], self, i + 1 - len(e.args[1]), i + 1 - len(e.args[1]) + e.args[3], *e.args[4:]
        )
    result = "".join(res)
    set_tainted_ranges(result, new_ranges)
    return result


def encode_aspect(self, *args, **kwargs):
    result = self.encode(*args, **kwargs)
    return result
