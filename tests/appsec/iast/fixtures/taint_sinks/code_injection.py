"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
from ast import literal_eval


def pt_eval(origin_string):
    r = eval(origin_string)
    return r


def pt_eval_globals(origin_string):
    context = {"x": 5, "y": 10}
    r = eval(origin_string, context)
    return r


def pt_eval_globals_locals(origin_string):
    z = 15  # noqa: F841
    globals_dict = {"x": 10}
    locals_dict = {"y": 20}
    r = eval(origin_string, globals_dict, locals_dict)
    return r


def pt_eval_lambda(fun):
    return eval("lambda v,fun=fun:not fun(v)")


def is_true(value):
    return value is True


def pt_eval_lambda_globals(origin_string):
    globals_dict = {"square": lambda x: x * x}
    r = eval(origin_string, globals=globals_dict)
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
