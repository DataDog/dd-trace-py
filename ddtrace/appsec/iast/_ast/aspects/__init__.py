#!/usr/bin/env python3

from builtins import str as builtin_str


def str_aspect(*args, **kwargs):
    return builtin_str(*args, **kwargs)


def add_aspect(op1, op2):
    if not isinstance(op1, (str, bytes, bytearray)):
        return op1 + op2
    return getattr(op1.__class__, "__add__")(op1, op2)
