#!/usr/bin/env python3

from builtins import str as builtin_str


def str_aspect(*args, **kwargs):
    return builtin_str(*args, **kwargs)


def add_aspect(op1, op2):
    return op1.__class__.__add__(op2)
