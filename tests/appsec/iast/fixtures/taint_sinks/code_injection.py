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


def pt_exec_with_globals(origin_string):
    my_var_in_pt_exec_with_globals = "abc"
    exec(origin_string)
    my_var_in_pt_exec_with_globals += "def"
    return my_var_in_pt_exec_with_globals
