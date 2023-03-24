#!/usr/bin/env python3

from ddtrace.appsec._asm_request_context import get_taint_dict
from ddtrace.appsec._asm_request_context import set_taint_dict
from ddtrace.appsec.iast._taint_tracking._native import new_pyobject_id
from ddtrace.appsec.iast._taint_tracking._native import setup  # noqa: F401


def add_taint_pyobject(pyobject, op1, op2):
    if not (is_pyobject_tainted(op1) or is_pyobject_tainted(op2)):
        return pyobject

    pyobject = new_pyobject_id(pyobject, len(pyobject))
    taint_dict = get_taint_dict()
    new_ranges = []
    if is_pyobject_tainted(op1):
        new_ranges = list(taint_dict[id(op1)])
    if is_pyobject_tainted(op2):
        offset = len(op1)
        for input_info, start, size in taint_dict[id(op2)]:
            new_ranges.append((input_info, start + offset, size))

    taint_dict[id(pyobject)] = tuple(new_ranges)
    set_taint_dict(taint_dict)
    return pyobject


def taint_pyobject(pyobject, input_info):
    if not pyobject:  # len(pyobject) < 1
        return pyobject
    assert input_info is not None
    len_pyobject = len(pyobject)
    pyobject = new_pyobject_id(pyobject, len_pyobject)
    taint_dict = get_taint_dict()
    taint_dict[id(pyobject)] = ((input_info, 0, len_pyobject),)
    set_taint_dict(taint_dict)
    return pyobject


def is_pyobject_tainted(pyobject):
    return id(pyobject) in get_taint_dict()


def set_tainted_ranges(pyobject, ranges):
    taint_dict = get_taint_dict()
    assert pyobject not in taint_dict
    taint_dict[id(pyobject)] = ranges
    set_taint_dict(taint_dict)


def get_tainted_ranges(pyobject):
    return get_taint_dict().get(id(pyobject), tuple())


def clear_taint_mapping():
    set_taint_dict({})
