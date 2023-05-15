# -*- encoding: utf-8 -*-
def inner():
    return 42


def traceme():
    cake = "🍰"  # noqa
    return 42 + inner()
