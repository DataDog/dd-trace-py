import threading

from ddtrace import compat

cdef extern from "<stdint.h>":
    ctypedef unsigned long long uint64_t


cdef class xorshift64s():
    cdef public uint64_t x

    def __init__(self):
        self.x = compat.getrandbits(64) ^ 4101842887655102017

    def __iter__(self):
        return self

    def __next__(self):
        self.x ^= self.x >> 21
        self.x ^= self.x << 35
        self.x ^= self.x >> 4
        return self.x * <uint64_t>2685821657736338717

    def next(self):
        return self.__next__()


loc = threading.local()


def get_cxorshift64s(*args, **kwargs):
    gen = getattr(loc, "cxorshift64s", None)
    if not gen:
        gen = xorshift64s(*args, **kwargs)
        setattr(loc, "cxorshift64s", gen)
    return gen
