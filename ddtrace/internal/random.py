import threading

from ddtrace import compat
import pyximport; pyximport.install()  # noqa
from . import rand
from .rand import get_cxorshift64s  # noqa


def xorshift64s():
    """Thread-safe generator for pseudorandom 64-bit integers.

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
    loc = threading.local()
    loc.x = compat.getrandbits(64) ^ 4101842887655102017

    while True:
        x = loc.x
        x ^= x >> 21
        x ^= x << 35
        # Python integers are unbounded
        x &= 0xFFFFFFFFFFFFFFFF
        x ^= x >> 4
        loc.x = x
        yield (x * 2685821657736338717) & 0xFFFFFFFFFFFFFFFF


def rand64bits(reseed_interval=0):
    """Thread-safe generator for pseudorandom 64-bit integers.

    An optional reseed interval can be specified. If > 0 then a random number
    will be retrieved and used as a base for subsequent calls in the interval.
    """
    i = 0
    r = compat.getrandbits(64)

    while True:
        if not reseed_interval:
            yield compat.getrandbits(64)
        else:
            if i == 0:
                r = compat.getrandbits(64)
            yield r + i
            i = 0 if i == reseed_interval else i + 1


loc = threading.local()


def get_rand64bits(*args, **kwargs):
    gen = getattr(loc, "rand64bits", None)
    if not gen:
        if compat.PY3:
            gen = rand64bits(*args, **kwargs)
        else:
            gen = rand.xorshift64s()
        setattr(loc, "rand64bits", gen)
    return gen


def get_xorshift64s(*args, **kwargs):
    gen = getattr(loc, "xorshift64s", None)
    if not gen:
        gen = xorshift64s(*args, **kwargs)
        setattr(loc, "xorshift64s", gen)
    return gen
