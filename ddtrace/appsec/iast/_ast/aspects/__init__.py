#!/usr/bin/env python3

from ddtrace.appsec.iast._taint_tracking import taint_pyobject
from ddtrace.appsec.iast._taint_tracking import is_pyobject_tainted

from builtins import str as builtin_str


def str_aspect(*args, **kwargs):
    result = builtin_str(*args, **kwargs)
    if isinstance(args[0], (str, bytes)) and is_pyobject_tainted(args[0]):
        print("is tainted!")
        taint_pyobject(result)

    return result


def add_aspect(op1, op2):
    result = op1.__class__.__add__(op2)
    if isinstance(op1, (str, bytes)) and is_pyobject_tainted(op1):
        print("is tainted!")
        taint_pyobject(result)

    return result
