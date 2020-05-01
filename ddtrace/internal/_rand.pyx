import threading

from ddtrace import compat

cdef extern from "<stdint.h>":
    ctypedef unsigned long long uint64_t


cdef class xorshift64s():
    """Generator for pseudorandom 64-bit integers.

    Implements the xorshift* algorithm with a non-linear transformation
    (multiplication) applied to the result.

    This implementation uses the recommended constants from Numerical
    Recipes Chapter 7 (Ranq1 algorithm).

    According to TPV, the period is approx. 1.8 x 10^19. So it should
    not be used by an application that makes more than 10^12 calls.

    To put this into perspective: we cap the max number of traces at
    1k/s let's be conservative and say each trace contains 100 spans.

    That's 100k spans/second which would be 100k + 1 calls to this fn
    per second.

    That's 10,000,000 seconds until we hit the period. That's 115 days
    of 100k spans/second (with no application restart) until the period
    is reached.
    """
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


def get_rand64_gen(*args, **kwargs):
    gen = getattr(loc, "xorshift64s", None)
    if not gen:
        gen = xorshift64s(*args, **kwargs)
        setattr(loc, "xorshift64s", gen)
    return gen
