#!/usr/bin/env python3


class FakeStr(str):
    def join(self, *args):
        return args[0] + args[1]

    def call_join(self, *args):
        return self.join(*args)
