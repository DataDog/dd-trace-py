#!/usr/bin/env python3


class ReObject:
    def __init__(self, pattern):
        self.pattern = pattern

    def sub(self, replacement, string):
        return "fake_replacement_0"

    def subn(self, replacement, string):
        return "fake_replacement_3", 0

    def split(pattern, string, *args, **kwargs):
        return ["fake_result_2", "fake_result_1", "fake_result_0"]


def sub(pattern, replacement, string):
    return "fake_replacement_1"


def subn(pattern, replacement, string):
    return "fake_replacement_2", 0


def split(pattern, string, *args, **kwargs):
    return ["fake_result_0", "fake_result_1", "fake_result_2"]


def compile(pattern):  # noqa: A001
    return ReObject(pattern)
