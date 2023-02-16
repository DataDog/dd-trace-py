#!/usr/bin/env python3

from builtins import str as builtin_str

from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted  # type: ignore[attr-defined]
from ddtrace.appsec.iast._taint_tracking import taint_pyobject  # type: ignore[attr-defined]


def str_aspect(*args, **kwargs):
    result = builtin_str(*args, **kwargs)
    if isinstance(args[0], (str, bytes, bytearray)) and is_pyobject_tainted(args[0]):
        taint_pyobject(result)

    return result


def add_aspect(op1, op2):
    if not isinstance(op1, (str, bytes, bytearray)):
        return op1 + op2

    result = getattr(op1.__class__, "__add__")(op1, op2)
    if is_pyobject_tainted(op1) or is_pyobject_tainted(op2):
        taint_pyobject(result)

    return result
