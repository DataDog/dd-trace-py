#!/usr/bin/env python3


class FakeStr(str):
    def ljust(self, *args):
        # This string is not propagating so it should not be tainted
        return "not_tainted"

    def call_ljust(self, candidate_text, *args):
        return self.ljust(10, args[0], candidate_text)

    def join(self, *args):
        # This string is not propagating so it should not be tainted
        return "not_tainted"

    def call_join(self, _, *args):
        return self.join(*args)
