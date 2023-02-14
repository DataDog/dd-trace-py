#!/usr/bin/env python3

from builtins import str as builtin_str


def str_aspect(*args, **kwargs):
    return builtin_str(*args, **kwargs)


def _not_implemented(_):
    return NotImplemented


def add_aspect(op1, op2):
    res = getattr(op1.__class__, "__add__", _not_implemented)(op2)
    if res is NotImplemented and op2.__class__ is not op1.__class__:
        res = getattr(op2.__class__, "__radd__", _not_implemented)(op1)
    if res is NotImplemented:
        raise TypeError(
            "unsupported operand type(s) for +: '%s' and '%s'" % (op1.__class__.__name__, op2.__class__.__name__)
        )
    return res
