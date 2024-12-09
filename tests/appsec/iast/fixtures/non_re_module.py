#!/usr/bin/env python3


class ReMatch:
    def __init__(self, pattern, string):
        self.pattern = pattern
        self.orig_string = string

    def group(self, index):
        return "fake_group_{}".format(index)

    def groups(self):
        return ("fake_group_0", "fake_group_1", "fake_group_2")

    def expand(self, string):
        return "fake_expand"

    @property
    def string(self):
        return "fake_string"


class ReObject:
    def __init__(self, pattern):
        self.pattern = pattern

    def findall(self, string):
        return ["fake_result_0", "fake_result_1", "fake_result_2"]

    def finditer(self, string):
        return iter(self.findall(string))

    def sub(self, replacement, string):
        return "fake_replacement_0"

    def subn(self, replacement, string):
        return "fake_replacement_3", 0

    def split(self, string, *args, **kwargs):
        return ["fake_result_2", "fake_result_1", "fake_result_0"]

    def search(self, string):
        return ReMatch(self.pattern, string)

    def fullmatch(self, string):
        return ReMatch(self.pattern, string)

    def match(self, string):
        return ReMatch(self.pattern, string)


def findall(pattern, string):
    return ["fake_result_2", "fake_result_1", "fake_result_0"]


def finditer(pattern, string):
    return iter(["fake_result_2", "fake_result_1", "fake_result_0"])


def sub(pattern, replacement, string):
    return "fake_replacement_1"


def subn(pattern, replacement, string):
    return "fake_replacement_2", 0


def split(pattern, string, *args, **kwargs):
    return ["fake_result_0", "fake_result_1", "fake_result_2"]


def fullmatch(pattern, string):
    return ReMatch(pattern, string)


def search(pattern, string):
    return ReMatch(pattern, string)


def match(pattern, string):
    return ReMatch(pattern, string)


def compile(pattern):  # noqa: A001
    return ReObject(pattern)
