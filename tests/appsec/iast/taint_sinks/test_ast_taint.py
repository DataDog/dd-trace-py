#!/usr/bin/env python3

from ddtrace.appsec._iast.taint_sinks.ast_taint import ast_function


class MyArray:
    def __init__(self, values):
        self.values = values

    def copy(self):
        return self.values.copy()

    def __bool__(self):
        if len(self.values) > 0:
            raise ValueError("Array is not empty")
        return False


def test_ast_function_with_valueerror_on_bool():
    values = MyArray([7, 19, 20, 35, 10, 42, 8])
    values_copy = ast_function(values.copy, False)
    assert values_copy == values.values
