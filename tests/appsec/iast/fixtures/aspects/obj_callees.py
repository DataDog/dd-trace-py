#!/usr/bin/env python3

import builtins as _builtins


def _number_generator():
    number = 0
    while True:
        number += 1
        yield FakeStr(_builtins.str(number))


class FakeStr(str):
    # Yeah, we want the join of the str to work as self.join, so we're not overriding
    # join or doing this aspect tests in this case
    #
    # def join(self, *args, **kwargs):
    #     return self.join(("join", _builtins.str(args), _builtins.str(kwargs)))

    def extend(self, *args, **kwargs):
        return self.join(("extend", _builtins.str(args), _builtins.str(kwargs)))

    def encode(self, *args, **kwargs):
        return self.join(("encode", _builtins.str(args), _builtins.str(kwargs)))

    def decode(self, *args, **kwargs):
        return self.join(("decode", _builtins.str(args), _builtins.str(kwargs)))

    def str(self, *args, **kwargs):  # noqa: A001
        return self.join(("_builtins.str", _builtins.str(args), _builtins.str(kwargs)))

    def bytes(self, *args, **kwargs):  # noqa: A001
        return self.join(("bytes", _builtins.str(args), _builtins.str(kwargs)))

    def bytearray(self, *args, **kwargs):  # noqa: A001
        return self.join(("bytearray", _builtins.str(args), _builtins.str(kwargs)))

    def ljust(self, *args, **kwargs):
        return self.join(("ljust", _builtins.str(args), _builtins.str(kwargs)))

    def zfill(self, *args, **kwargs):
        return self.join(("zfill", _builtins.str(args), _builtins.str(kwargs)))

    def format(self, *args, **kwargs):  # noqa: A001
        return self.join(("format", _builtins.str(args), _builtins.str(kwargs)))

    def format_map(self, *args, **kwargs):
        return self.join(("format_map", _builtins.str(args), _builtins.str(kwargs)))

    def repr(self, *args, **kwargs):  # noqa: A001
        return self.join(("repr", _builtins.str(args), _builtins.str(kwargs)))

    def upper(self, *args, **kwargs):
        return self.join(("upper", _builtins.str(args), _builtins.str(kwargs)))

    def lower(self, *args, **kwargs):
        return self.join(("lower", _builtins.str(args), _builtins.str(kwargs)))

    def swapcase(self, *args, **kwargs):
        return self.join(("swapcase", _builtins.str(args), _builtins.str(kwargs)))

    def title(self, *args, **kwargs):
        return self.join(("title", _builtins.str(args), _builtins.str(kwargs)))

    def capitalize(self, *args, **kwargs):
        return self.join(("capitalize", _builtins.str(args), _builtins.str(kwargs)))

    def casefold(self, *args, **kwargs):
        return self.join(("casefold", _builtins.str(args), _builtins.str(kwargs)))

    def translate(self, *args, **kwargs):
        return self.join(("translate", _builtins.str(args), _builtins.str(kwargs)))
