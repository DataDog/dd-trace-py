#!/usr/bin/env python3

import tests.appsec.iast.fixtures.aspects.callees as callees_module


class FakeStr(str):
    def __init__(self, *args, **kwargs):
        for i in [x for x in dir(callees_module) if not x.startswith(("_", "@"))]:
            setattr(self, i, getattr(callees_module, i))

    def call_ljust(self, *args, **kwargs):
        return self.ljust(*args, **kwargs)

    def call_join(self, *args, **kwargs):
        return self.join(*args, **kwargs)

    def call_zfill(self, *args, **kwargs):
        return self.zfill(*args, **kwargs)

    def call_format(self, *args, **kwargs):
        return self.format(*args, **kwargs)

    def call_format_map(self, *args, **kwargs):
        return self.format_map(*args, **kwargs)

    def call_repr(self, *args, **kwargs):
        return self.repr(*args, **kwargs)

    def call_upper(self, *args, **kwargs):
        return self.upper(*args, **kwargs)

    def call_lower(self, *args, **kwargs):
        return self.lower(*args, **kwargs)

    def call_casefold(self, *args, **kwargs):
        return self.casefold(*args, **kwargs)

    def call_capitalize(self, *args, **kwargs):
        return self.capitalize(*args, **kwargs)

    def call_title(self, *args, **kwargs):
        return self.title(*args, **kwargs)

    def call_swapcase(self, *args, **kwargs):
        return self.swapcase(*args, **kwargs)

    def call_translate(self, *args, **kwargs):
        return self.translate(*args, **kwargs)

    def call_extend(self, *args, **kwargs):
        return self.extend(*args, **kwargs)

    def call_encode(self, *args, **kwargs):
        return self.encode(*args, **kwargs)

    def call_decode(self, *args, **kwargs):
        return self.decode(*args, **kwargs)

    def call_str(self, *args, **kwargs):
        return self.str(*args, **kwargs)

    def call_bytes(self, *args, **kwargs):
        return self.bytes(*args, **kwargs)

    def call_bytearray(self, *args, **kwargs):
        return self.bytearray(*args, **kwargs)
