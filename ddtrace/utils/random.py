import threading

from .. import compat


def ranq2():
    """Generator for pseudorandom 64-bit integers.

    Implements an xorshift generator described by Marsaglia.

    This algorithm is from Numeric Recipes Chapter 7 (Ranq2).
    """
    j = compat.getrandbits(64)
    v = 4101842887655102017
    w = 1

    v ^= j

    def int64():
        nonlocal v, w
        v ^= v >> 17
        v ^= v << 31
        v &= 0xFFFFFFFFFFFFFFFF
        v ^= v >> 8
        w = 4294957665 * (w & 0xFFFFFFFF)
        w &= 0xFFFFFFFFFFFFFFFF
        w = w >> 32
        return v ^ w

    v = int64()
    w = int64()
    lock = threading.Lock()

    while True:
        with lock:
            yield int64()


def xorshift64s():
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
    x = compat.getrandbits(64) ^ 4101842887655102017
    lock = threading.Lock()

    while True:
        with lock:
            x ^= x >> 21
            x ^= x << 35
            # Python integers are unbounded
            x &= 0xFFFFFFFFFFFFFFFF
            x ^= x >> 4
            yield (x * 2685821657736338717) & 0xFFFFFFFFFFFFFFFF


rand64 = xorshift64s()
