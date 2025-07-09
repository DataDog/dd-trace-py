import os

from ddtrace.appsec._iast._stacktrace import get_info_frame


CWD = os.path.abspath(os.getcwd())


def func_1(a, b, c):
    return func_2(a, b, c)


def func_2(a, b, c):
    return func_3(a, b, c)


def func_3(a, b, c):
    return func_4(a, b, c)


def func_4(a, b, c):
    return func_5(a, b, c)


def func_5(a, b, c):
    return func_6(a, b, c)


def func_6(a, b, c):
    return func_7(a, b, c)


def func_7(a, b, c):
    return func_8(a, b, c)


def func_8(a, b, c):
    return func_9(a, b, c)


def func_9(a, b, c):
    return func_10(a, b, c)


def func_10(a, b, c):
    return func_11(a, b, c)


def func_11(a, b, c):
    return func_12(a, b, c)


def func_12(a, b, c):
    return func_13(a, b, c)


def func_13(a, b, c):
    return func_14(a, b, c)


def func_14(a, b, c):
    return func_15(a, b, c)


def func_15(a, b, c):
    return func_16(a, b, c)


def func_16(a, b, c):
    return func_17(a, b, c)


def func_17(a, b, c):
    return func_18(a, b, c)


def func_18(a, b, c):
    return func_19(a, b, c)


def func_19(a, b, c):
    return func_20(a, b, c)


def func_20(a, b, c):
    func = get_info_frame
    frame_info = func()
    return frame_info
