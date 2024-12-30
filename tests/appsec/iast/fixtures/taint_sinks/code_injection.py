"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
from ast import literal_eval


def pt_eval(origin_string):
    r = eval(origin_string)
    return r


def pt_literal_eval(origin_string):
    r = literal_eval(origin_string)
    return r


def pt_exec(origin_string):
    exec(origin_string)
    return "OR: " + origin_string
